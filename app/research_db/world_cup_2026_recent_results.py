from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.config import ROOT

from .world_cup_2026_bootstrap import (
    DEFAULT_OUTPUT_DIR,
    NATIONAL_RECENT_RESULT_FIELDNAMES,
    TEAM_FIELDNAMES,
    read_csv,
    read_json,
    write_csv,
    write_json,
)

DEFAULT_SKILL_SCRIPTS_DIR = Path(
    os.environ.get(
        "SPORTS_STABLE_CRAWL_SCRIPTS_DIR",
        str(ROOT / "vendor" / "sports-stable-crawl" / "scripts"),
    )
)
DEFAULT_RECENT_MATCH_LIMIT = 5
RECENT_RESULT_FALLBACK_MODE = "embedded_fixture_matches"
TEAM_SEARCH_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "cabo verde": ("Cape Verde",),
    "congo dr": ("DR Congo",),
    "cote d ivoire": ("Ivory Coast", "Cote d Ivoire"),
    "curacao": ("Curacao",),
    "czechia": ("Czech Republic",),
}


def search_url(query: str) -> str:
    return f"https://www.whoscored.com/Search/?t={quote(query)}"


def extract_search_team_links(html: str) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    links: list[dict[str, str]] = []
    seen = set()
    for anchor in soup.select('a[href*="/teams/"]'):
        href = str(anchor.get("href") or "").strip()
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if not href or not label:
            continue
        key = (label.casefold(), href)
        if key in seen:
            continue
        seen.add(key)
        links.append({"label": label, "url": href})
    return links


def select_best_team_link(team_name: str, aliases: list[str], links: list[dict[str, str]]) -> dict[str, str] | None:
    wanted = {_normalize(team_name), *(_normalize(alias) for alias in aliases if alias)}
    if not wanted:
        return None

    scored: list[tuple[tuple[int, int, int, int, int], dict[str, str]]] = []
    for index, link in enumerate(links):
        label = _normalize(link["label"])
        url = link["url"]
        if "/teams/" not in url:
            continue
        if label not in wanted and not any(label.startswith(item + " ") for item in wanted):
            continue
        score = (
            0 if label == _normalize(team_name) else 1 if label in wanted else 2,
            0 if _is_senior_mens_label(link["label"]) else 1,
            0 if _normalize(team_name).replace(" ", "-") in url.casefold() else 1,
            0 if url.startswith("https://www.whoscored.com/teams/") or url.startswith("/teams/") else 1,
            index,
        )
        scored.append((score, link))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return scored[0][1]


def ensure_team_row(
    known_rows: dict[str, dict[str, str]],
    team_rows: list[dict[str, str]],
    team_name: str,
    *,
    accessed_at: str,
) -> dict[str, str]:
    normalized = _normalize(team_name)
    existing = known_rows.get(normalized)
    if existing is not None:
        return existing
    team_id = f"team_{_slug(team_name)}"
    row = {
        "team_id": team_id,
        "canonical_name": team_name,
        "country_code": "",
        "fifa_code": "",
        "source_team_id": f"whoscored_team_name:{_slug(team_name)}",
        "confederation": "",
        "stage": "",
        "host_team": "false",
        "available_at": accessed_at,
        "aliases": team_name,
    }
    team_rows.append(row)
    known_rows[normalized] = row
    return row


def build_recent_result_row(
    match: dict[str, Any],
    *,
    current_team_name: str,
    team_rows: list[dict[str, str]],
    known_rows: dict[str, dict[str, str]],
    accessed_at: str,
) -> dict[str, str]:
    home_team = ensure_team_row(known_rows, team_rows, str(match["home_team"]), accessed_at=accessed_at)
    away_team = ensure_team_row(known_rows, team_rows, str(match["away_team"]), accessed_at=accessed_at)
    current_team = ensure_team_row(known_rows, team_rows, current_team_name, accessed_at=accessed_at)
    match_id = match_source_id(str(match["match_url"]))
    opponent_team_id = away_team["team_id"] if current_team["team_id"] == home_team["team_id"] else home_team["team_id"]
    home_score, away_score = _home_away_score(match)
    return {
        "fixture_id": f"recent_result_{match_id}",
        "competition": str(match.get("tournament") or ""),
        "season": str(_season_from_date(str(match.get("date") or "")) or ""),
        "match_time": f"{match['date']}T00:00:00+00:00",
        "home_team_id": home_team["team_id"],
        "away_team_id": away_team["team_id"],
        "neutral_field": "true" if match.get("team_side") == "neutral" else "false",
        "result_status": "closed",
        "home_score": str(home_score),
        "away_score": str(away_score),
        "opponent_team_id": opponent_team_id,
        "source_fixture_id": match_id,
        "source_result_id": f"{match_id}:result",
        "available_at": accessed_at,
    }


async def enrich_recent_results(
    *,
    output_dir: Path,
    accessed_at: str,
    recent_match_limit: int,
    selected_only: bool,
    skill_scripts_dir: Path,
    only_team_ids: set[str] | None,
    preserve_existing: bool,
) -> dict[str, Any]:
    manifest = read_json(output_dir / "source_manifest.json")
    team_rows = read_csv(output_dir / "world_cup_2026_teams.csv")
    selected_team_ids = set(str(item) for item in manifest.get("selected_team_ids", []))
    target_rows = [
        row for row in team_rows
        if not selected_only or row["team_id"] in selected_team_ids
    ]
    if only_team_ids:
        target_rows = [row for row in target_rows if row["team_id"] in only_team_ids]
    replacement_team_ids = _replacement_team_ids(team_rows, target_rows) if only_team_ids else set()
    if replacement_team_ids:
        team_rows = _filter_replaced_support_rows(
            team_rows,
            replacement_team_ids=replacement_team_ids,
            target_team_ids={row["team_id"] for row in target_rows},
        )
    known_rows: dict[str, dict[str, str]] = {}
    for row in team_rows:
        _register_known_team_row(known_rows, row)

    workflow_module = _load_workflow_module(skill_scripts_dir)
    workflow_cls = workflow_module.WhoScoredWorkflow
    diagnostics: list[dict[str, Any]] = []
    recent_result_rows = _load_existing_recent_result_rows(
        output_dir,
        preserve_existing=preserve_existing,
        replacement_team_ids=replacement_team_ids,
    )
    seen_fixtures = {
        row["source_fixture_id"]
        for row in recent_result_rows
        if row.get("source_fixture_id")
    }

    async with workflow_cls(session_id="ws-wc2026-recent-results") as workflow:
        for row in target_rows:
            aliases = [alias.strip() for alias in str(row.get("aliases") or "").split("|") if alias.strip()]
            search_queries = build_search_queries(row["canonical_name"], aliases)
            search_links: list[dict[str, str]] = []
            selected_link: dict[str, str] | None = None
            selected_query = row["canonical_name"]
            search_error = None
            for query in search_queries:
                try:
                    search_html = await workflow.fetch_html(search_url(query))
                    search_links = extract_search_team_links(search_html)
                    selected_link = select_best_team_link(row["canonical_name"], [*aliases, query], search_links)
                    if selected_link is not None:
                        selected_query = query
                        break
                except Exception as exc:  # noqa: BLE001
                    search_error = str(exc)
            if selected_link is None:
                diagnostics.append({
                    "team_id": row["team_id"],
                    "canonical_name": row["canonical_name"],
                    "status": "search_failed",
                    "search_query": selected_query,
                    "links_found": len(search_links),
                    "error": search_error,
                })
                continue

            team_url = _absolute_whoscored_url(selected_link["url"])
            try:
                team_html = await workflow.fetch_html(team_url)
                team_record, fallback = _parse_team_record(
                    workflow_module=workflow_module,
                    html=team_html,
                    page_url=team_url,
                    current_team_name=row["canonical_name"],
                    recent_match_limit=recent_match_limit,
                )
            except Exception as exc:  # noqa: BLE001
                diagnostics.append({
                    "team_id": row["team_id"],
                    "canonical_name": row["canonical_name"],
                    "status": "team_record_failed",
                    "search_query": selected_query,
                    "team_url": team_url,
                    "error": str(exc),
                })
                continue

            team_status = team_record.get("status", "error")
            team_rows_found = 0
            for match in team_record.get("recent_matches", []):
                source_fixture_id = match_source_id(str(match.get("match_url") or ""))
                if not source_fixture_id or source_fixture_id in seen_fixtures:
                    continue
                seen_fixtures.add(source_fixture_id)
                recent_result_rows.append(
                    build_recent_result_row(
                        match,
                        current_team_name=row["canonical_name"],
                        team_rows=team_rows,
                        known_rows=known_rows,
                        accessed_at=accessed_at,
                    )
                )
                team_rows_found += 1

            diagnostics.append({
                "team_id": row["team_id"],
                "canonical_name": row["canonical_name"],
                "status": team_status,
                "search_query": selected_query,
                "team_url": team_url,
                "links_found": len(search_links),
                "recent_matches": team_rows_found,
                "missing_fields": team_record.get("missing_fields", []),
            })
            if fallback is not None:
                diagnostics[-1]["fallback"] = fallback

    recent_result_rows.sort(key=lambda item: (item["match_time"], item["fixture_id"]))
    team_rows.sort(key=lambda item: item["team_id"])
    write_csv(output_dir / "world_cup_2026_teams.csv", TEAM_FIELDNAMES, team_rows)
    write_csv(output_dir / "national_recent_results.csv", NATIONAL_RECENT_RESULT_FIELDNAMES, recent_result_rows)
    diagnostics_payload = {
        "accessed_at": accessed_at,
        "recent_match_limit": recent_match_limit,
        "target_teams": len(target_rows),
        "rows_written": len(recent_result_rows),
        "preserve_existing": preserve_existing,
        "selected_team_filter": sorted(only_team_ids) if only_team_ids else [],
        "diagnostics": diagnostics,
    }
    write_json(output_dir / "recent_results_diagnostics.json", diagnostics_payload)
    return diagnostics_payload


def match_source_id(match_url: str) -> str:
    parts = str(match_url.rstrip("/")).split("/")
    if len(parts) < 3:
        return _slug(match_url)
    return parts[-3] if parts[-2] == "show" else parts[-2]


def _season_from_date(value: str) -> int | None:
    if len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def build_search_queries(team_name: str, aliases: list[str]) -> list[str]:
    queries = [team_name, *aliases]
    normalized_team = _normalize(team_name)
    queries.extend(TEAM_SEARCH_QUERY_ALIASES.get(normalized_team, ()))
    for value in [team_name, *aliases]:
        ascii_value = _ascii_fold(value)
        if ascii_value and ascii_value != value:
            queries.append(ascii_value)
    return _dedupe_search_queries(queries)


def _load_existing_recent_result_rows(
    output_dir: Path,
    *,
    preserve_existing: bool,
    replacement_team_ids: set[str],
) -> list[dict[str, str]]:
    path = output_dir / "national_recent_results.csv"
    if not preserve_existing or not path.exists():
        return []
    rows = read_csv(path)
    if not replacement_team_ids:
        return rows
    return [
        row
        for row in rows
        if row.get("home_team_id") not in replacement_team_ids
        and row.get("away_team_id") not in replacement_team_ids
    ]


def _load_workflow_module(skill_scripts_dir: Path):
    if not skill_scripts_dir.exists():
        raise FileNotFoundError(
            f"sports_stable_crawl_scripts_dir_missing:{skill_scripts_dir}"
        )
    scripts_dir = str(skill_scripts_dir)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import whoscored_workflow

    return whoscored_workflow


def _absolute_whoscored_url(url: str) -> str:
    return url if url.startswith("http") else f"https://www.whoscored.com{url}"


def _is_senior_mens_label(label: str) -> bool:
    lowered = label.casefold()
    blocked_tokens = ("(w)", "women", " u", " u-", " u17", " u20", " u21", " u23", " ii")
    return not any(token in lowered for token in blocked_tokens)


def _ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return normalized.encode("ascii", "ignore").decode("ascii")


def _team_identity_keys(row: dict[str, str]) -> set[str]:
    keys = {_normalize(row.get("canonical_name") or "")}
    for alias in str(row.get("aliases") or "").split("|"):
        normalized = _normalize(alias)
        if normalized:
            keys.add(normalized)
    keys.update(_normalize(alias) for alias in TEAM_SEARCH_QUERY_ALIASES.get(_normalize(row.get("canonical_name") or ""), ()))
    return {key for key in keys if key}


def _register_known_team_row(known_rows: dict[str, dict[str, str]], row: dict[str, str]) -> None:
    for key in _team_identity_keys(row):
        known_rows.setdefault(key, row)


def _replacement_team_ids(team_rows: list[dict[str, str]], target_rows: list[dict[str, str]]) -> set[str]:
    target_keys = set()
    for row in target_rows:
        target_keys.update(_team_identity_keys(row))
    replacement_ids = set()
    for row in team_rows:
        if _team_identity_keys(row) & target_keys:
            replacement_ids.add(row["team_id"])
    return replacement_ids


def _filter_replaced_support_rows(
    team_rows: list[dict[str, str]],
    *,
    replacement_team_ids: set[str],
    target_team_ids: set[str],
) -> list[dict[str, str]]:
    filtered = []
    for row in team_rows:
        team_id = row["team_id"]
        if team_id in target_team_ids:
            filtered.append(row)
            continue
        if team_id in replacement_team_ids and str(row.get("source_team_id") or "").startswith("whoscored_team_name:"):
            continue
        filtered.append(row)
    return filtered


def _normalize(value: str) -> str:
    ascii_value = _ascii_fold(value).casefold()
    cleaned = re.sub(r"[^a-z0-9]+", " ", ascii_value)
    return " ".join(cleaned.split())


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "_" for char in value.strip())
    return "_".join(part for part in slug.split("_") if part)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        normalized = _normalize(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(value)
    return ordered


def _dedupe_search_queries(values: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        cleaned = " ".join(str(value or "").casefold().split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(value)
    return ordered


def _home_away_score(match: dict[str, Any]) -> tuple[int, int]:
    if match.get("home_score") is not None and match.get("away_score") is not None:
        return int(match["home_score"]), int(match["away_score"])
    team_side = str(match.get("team_side") or "")
    team_score = int(match.get("team_score") or 0)
    opponent_score = int(match.get("opponent_score") or 0)
    if team_side == "away":
        return opponent_score, team_score
    return team_score, opponent_score


def _parse_team_record(
    *,
    workflow_module: Any,
    html: str,
    page_url: str,
    current_team_name: str,
    recent_match_limit: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    primary_error = None
    try:
        primary_record = workflow_module.parse_team_page(
            html=html,
            page_url=page_url,
            recent_match_limit=recent_match_limit,
        )
    except Exception as exc:  # noqa: BLE001
        primary_record = None
        primary_error = str(exc)

    fallback_record = _parse_team_page_from_fixture_matches(
        html=html,
        page_url=page_url,
        current_team_name=current_team_name,
        recent_match_limit=recent_match_limit,
    )

    if fallback_record.get("recent_matches"):
        if primary_record is None or not primary_record.get("recent_matches"):
            fallback = {
                "mode": RECENT_RESULT_FALLBACK_MODE,
                "reason": primary_error or "recent_matches_not_found_in_primary_parser",
            }
            if primary_record and primary_record.get("candidate_player_links"):
                fallback_record["candidate_player_links"] = primary_record["candidate_player_links"]
                fallback_record["missing_fields"] = sorted(
                    set(fallback_record.get("missing_fields", []))
                    - {"candidate_player_links_not_found"}
                )
                if not fallback_record["missing_fields"]:
                    fallback_record["status"] = "ok"
            return fallback_record, fallback

    if primary_record is None:
        raise ValueError(primary_error or "primary_team_parser_failed")
    return primary_record, None


def _parse_team_page_from_fixture_matches(
    *,
    html: str,
    page_url: str,
    current_team_name: str,
    recent_match_limit: int,
) -> dict[str, Any]:
    team_name = _extract_team_name_from_title(html) or current_team_name
    candidate_player_links = _extract_candidate_player_links(html)
    recent_matches = _parse_recent_matches_from_fixture_blob(
        html=html,
        current_team_name=team_name,
        recent_match_limit=recent_match_limit,
    )
    missing_fields = [
        "opponent_strength_not_available_on_supported_whoscored_path",
    ]
    if not team_name:
        missing_fields.append("team_name_not_found")
    if not recent_matches:
        missing_fields.append("recent_matches_not_found")
    if not candidate_player_links:
        missing_fields.append("candidate_player_links_not_found")
    status = "ok" if recent_matches and candidate_player_links else "partial"
    return {
        "source": "whoscored",
        "record_type": "national_team_recent_matches",
        "page_url": page_url,
        "team": team_name,
        "recent_matches": recent_matches,
        "candidate_player_links": candidate_player_links,
        "status": status,
        "missing_fields": sorted(set(missing_fields)),
    }


def _extract_team_name_from_title(html: str) -> str:
    match = re.search(r"<title>\s*(.*?)\s*</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return match.group(1).split(" - ", 1)[0].strip()


def _extract_candidate_player_links(html: str) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    links: list[dict[str, str]] = []
    seen = set()
    for anchor in soup.select('a[href*="/players/"]'):
        href = str(anchor.get("href") or "").strip()
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if not href or not label:
            continue
        url = _absolute_whoscored_url(href)
        key = (label.casefold(), url)
        if key in seen:
            continue
        seen.add(key)
        links.append({"label": label, "url": url})
        if len(links) >= 26:
            break
    return links


def _parse_recent_matches_from_fixture_blob(
    *,
    html: str,
    current_team_name: str,
    recent_match_limit: int,
) -> list[dict[str, Any]]:
    matches_block = _extract_fixture_matches_block(html)
    if not matches_block:
        return []
    try:
        raw_matches = ast.literal_eval(matches_block)
    except (SyntaxError, ValueError):
        return []
    recent_matches: list[dict[str, Any]] = []
    for match in raw_matches:
        parsed = _parse_fixture_match(match=match, current_team_name=current_team_name)
        if parsed is None:
            continue
        recent_matches.append(parsed)
        if len(recent_matches) >= recent_match_limit:
            break
    return recent_matches


def _extract_fixture_matches_block(html: str) -> str | None:
    marker = "fixtureMatches:["
    marker_index = html.find(marker)
    if marker_index == -1:
        return None
    matches_index = html.find("[[", marker_index + len(marker))
    if matches_index == -1:
        return None
    return _extract_bracket_block(html, matches_index)


def _extract_bracket_block(text: str, start_index: int) -> str | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start_index, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start_index:index + 1]
    return None


def _parse_fixture_match(*, match: Any, current_team_name: str) -> dict[str, Any] | None:
    if not isinstance(match, (list, tuple)) or len(match) < 17:
        return None
    try:
        match_id = str(match[0])
        date_value = _parse_whoscored_date(str(match[2]))
        home_team = str(match[5])
        away_team = str(match[8])
        tournament = str(match[16])
    except (TypeError, ValueError):
        return None
    if not match_id or not date_value or not home_team or not away_team:
        return None

    home_score = _coerce_score_value(match[-2])
    away_score = _coerce_score_value(match[-1])
    if home_score is None or away_score is None:
        home_score, away_score = _parse_score_display(str(match[10] if len(match) > 10 else ""))
    if home_score is None or away_score is None:
        return None

    normalized_current = _normalize(current_team_name)
    if normalized_current == _normalize(home_team):
        team_side = "home"
        opponent = away_team
        team_score = home_score
        opponent_score = away_score
    elif normalized_current == _normalize(away_team):
        team_side = "away"
        opponent = home_team
        team_score = away_score
        opponent_score = home_score
    else:
        team_side = "neutral"
        opponent = away_team
        team_score = home_score
        opponent_score = away_score

    return {
        "date": date_value,
        "tournament": tournament,
        "home_team": home_team,
        "away_team": away_team,
        "team": current_team_name,
        "opponent": opponent,
        "team_side": team_side,
        "team_score": team_score,
        "opponent_score": opponent_score,
        "home_score": home_score,
        "away_score": away_score,
        "match_url": f"https://www.whoscored.com/matches/{match_id}/show",
    }


def _parse_whoscored_date(value: str) -> str | None:
    for pattern in ("%d-%m-%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _coerce_score_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    cleaned = re.sub(r"[^0-9-]", "", str(value))
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_score_display(value: str) -> tuple[int | None, int | None]:
    parts = re.split(r"\s*:\s*", str(value).strip(), maxsplit=1)
    if len(parts) != 2:
        return None, None
    return _coerce_score_value(parts[0]), _coerce_score_value(parts[1])


async def _async_main(args: argparse.Namespace) -> None:
    result = await enrich_recent_results(
        output_dir=args.output_dir,
        accessed_at=args.accessed_at,
        recent_match_limit=args.recent_match_limit,
        selected_only=not args.include_support_teams,
        skill_scripts_dir=args.skill_scripts_dir,
        only_team_ids=set(args.team_id or []) or None,
        preserve_existing=not args.replace_existing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--accessed-at", required=True)
    parser.add_argument("--recent-match-limit", type=int, default=DEFAULT_RECENT_MATCH_LIMIT)
    parser.add_argument("--include-support-teams", action="store_true")
    parser.add_argument("--skill-scripts-dir", type=Path, default=DEFAULT_SKILL_SCRIPTS_DIR)
    parser.add_argument("--team-id", action="append", default=[])
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()

    import asyncio

    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
