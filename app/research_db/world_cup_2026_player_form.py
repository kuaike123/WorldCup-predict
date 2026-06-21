from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from .world_cup_2026_bootstrap import CORE_PLAYER_FIELDNAMES, DEFAULT_OUTPUT_DIR, read_csv, read_json, write_csv, write_json
from .world_cup_2026_recent_results import (
    DEFAULT_SKILL_SCRIPTS_DIR,
    TEAM_SEARCH_QUERY_ALIASES,
    build_search_queries,
    extract_search_team_links,
    search_url,
    select_best_team_link,
)

DEFAULT_RECENT_CLUB_MATCH_LIMIT = 5
DEFAULT_MAX_PLAYERS_PER_TEAM = 8
DEFAULT_MIN_PLAYERS_PER_TEAM = 3
DEFAULT_PLAYER_PAGE_TIMEOUT_MS = 45000
PLAYER_URL_CACHE_FILE = "player_url_cache.json"
PLAYER_SEARCH_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "raul rangel": ("Jose Rangel",),
}
PLAYER_DIRECT_URL_ALIASES: dict[str, str] = {
    "gervane kastaneer": "https://www.whoscored.com/players/119109/fixtures/gervane-kastaneer",
    "tyrick bodak": "https://www.whoscored.com/players/455927/fixtures/tyrick-bodak",
    "jeremy antonisse": "https://www.whoscored.com/players/409538/fixtures/jeremy-antonisse",
    "eloy room": "https://www.whoscored.com/players/67114/fixtures/eloy-room",
    "ar jany martha": "https://www.whoscored.com/players/422800/fixtures/ar-jany-martha",
}
PLAYER_FORM_SOURCE = "stable_whoscored_player_fixtures_summary"
PLAYER_FORM_FIELDNAMES = (
    "snapshot_id",
    "player_id",
    "team_id",
    "club_name",
    "club_source_id",
    "as_of",
    "club_recent_matches",
    "club_recent_starts",
    "club_recent_minutes",
    "club_recent_goals",
    "club_recent_assists",
    "national_recent_caps",
    "national_recent_starts",
    "national_recent_minutes",
    "national_recent_goals",
    "national_recent_assists",
    "source",
    "source_player_id",
    "available_at",
)


def select_best_player_link(player_name: str, links: list[dict[str, str]]) -> dict[str, str] | None:
    target = _normalize_name(player_name)
    if not target:
        return None
    target_tokens = target.split()
    target_last = target_tokens[-1] if target_tokens else ""
    target_first = target_tokens[0] if target_tokens else ""
    scored: list[tuple[tuple[int, int, int, int, int], dict[str, str]]] = []
    for index, link in enumerate(links):
        label = _normalize_name(link["label"])
        if not label:
            continue
        label_tokens = label.split()
        overlap = len(set(target_tokens) & set(label_tokens))
        last_match = int(bool(target_last and label_tokens and label_tokens[-1] == target_last))
        first_match = int(bool(target_first and label_tokens and label_tokens[0] == target_first))
        initial_match = int(bool(target_first and label_tokens and label_tokens[0].startswith(target_first[:1])))
        if overlap == 0 and not last_match:
            continue
        ratio = _token_similarity(target_tokens, label_tokens)
        if last_match == 0 and ratio < 65:
            continue
        score = (
            0 if label == target else 1,
            0 if last_match else 1,
            -overlap,
            0 if first_match or initial_match else 1,
            index,
        )
        scored.append((score, link))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return scored[0][1]


async def enrich_player_form(
    *,
    output_dir: Path,
    accessed_at: str,
    recent_club_match_limit: int,
    max_players_per_team: int,
    page_timeout_ms: int,
    selected_only: bool,
    skill_scripts_dir: Path,
    only_team_ids: set[str] | None,
    preserve_existing: bool,
) -> dict[str, Any]:
    manifest = read_json(output_dir / "source_manifest.json")
    team_rows = read_csv(output_dir / "world_cup_2026_teams.csv")
    core_player_rows = read_csv(output_dir / "core_players.csv")
    strength_rows = [
        item
        for item in read_json(output_dir / "team_strength_snapshots.json")
        if isinstance(item, dict)
    ]
    selected_team_ids = set(str(item) for item in manifest.get("selected_team_ids", []))
    target_team_rows = [
        row
        for row in team_rows
        if row["team_id"] in {player["team_id"] for player in core_player_rows}
        and (not selected_only or row["team_id"] in selected_team_ids)
    ]
    if only_team_ids:
        target_team_rows = [row for row in target_team_rows if row["team_id"] in only_team_ids]

    team_player_targets = build_team_player_target_counts(
        target_team_rows,
        strength_rows,
        max_players_per_team=max_players_per_team,
    )
    core_players_by_team = _core_players_by_team(
        core_player_rows,
        max_players_per_team=max_players_per_team,
        selected_team_ids={row["team_id"] for row in target_team_rows},
    )
    existing_snapshots = _load_existing_player_form_rows(output_dir, preserve_existing=preserve_existing)
    snapshot_rows: list[dict[str, Any]] = existing_snapshots[:]
    diagnostics: list[dict[str, Any]] = []
    player_url_cache = _load_player_url_cache(output_dir)

    workflow_module = _load_workflow_module(skill_scripts_dir)
    workflow_cls = workflow_module.WhoScoredWorkflow
    session_id = "ws-wc2026-player-form"

    async with workflow_cls(session_id=session_id) as workflow:
        if hasattr(workflow_module, "build_run_config"):
            workflow._config = workflow_module.build_run_config(session_id=session_id, page_timeout_ms=page_timeout_ms)
        for team_row in target_team_rows:
            team_id = team_row["team_id"]
            players = core_players_by_team.get(team_id, [])
            target_players = team_player_targets.get(team_id, max_players_per_team)
            strength_rank = _team_strength_rank(team_id, strength_rows)
            team_url, team_record, team_record_error = await _team_record_for_row(
                workflow=workflow,
                workflow_module=workflow_module,
                team_row=team_row,
                recent_club_match_limit=recent_club_match_limit,
            )
            candidate_links = []
            if isinstance(team_record, dict):
                candidate_links = list(team_record.get("candidate_player_links") or [])
            team_diagnostic = {
                "team_id": team_id,
                "canonical_name": team_row["canonical_name"],
                "target_players": min(target_players, len(players)),
                "candidate_players": len(players),
                "team_strength_rank": strength_rank,
                "team_url": team_url,
                "team_record_status": team_record.get("status") if isinstance(team_record, dict) else None,
                "team_record_error": team_record_error,
                "team_page_candidate_links": len(candidate_links),
                "mapped_players": 0,
                "snapshots_built": 0,
                "status": "ok",
                "player_results": [],
            }
            for player_row in players:
                if team_diagnostic["snapshots_built"] >= target_players:
                    break
                player_result = {
                    "player_id": player_row["player_id"],
                    "canonical_name": player_row["canonical_name"],
                    "selection_rank": player_row["selection_rank"],
                }
                selected_link, resolution = await _resolve_player_link(
                    workflow=workflow,
                    player_row=player_row,
                    team_row=team_row,
                    candidate_links=candidate_links,
                    player_url_cache=player_url_cache,
                )
                player_result.update(resolution)
                if selected_link is None:
                    player_result["status"] = "unmapped_player_link"
                    team_diagnostic["player_results"].append(player_result)
                    continue
                player_result["player_url"] = selected_link["url"]
                try:
                    player_html = await workflow.fetch_html(selected_link["url"])
                    player_record = workflow_module.parse_player_fixtures_page(
                        html=player_html,
                        page_url=selected_link["url"],
                        recent_club_match_limit=recent_club_match_limit,
                    )
                except Exception as exc:  # noqa: BLE001
                    player_result["status"] = "player_record_failed"
                    player_result["error"] = str(exc)
                    team_diagnostic["player_results"].append(player_result)
                    continue

                snapshot = build_player_form_snapshot(
                    player_row=player_row,
                    team_row=team_row,
                    player_record=player_record,
                    player_html=player_html,
                    accessed_at=accessed_at,
                )
                snapshot_rows.append(snapshot)
                _update_core_player_row(player_row, snapshot)
                _update_player_url_cache(
                    player_url_cache,
                    player_row=player_row,
                    selected_link=selected_link,
                    resolution=resolution,
                    accessed_at=accessed_at,
                )
                player_result["status"] = player_record.get("status", "partial")
                player_result["club_name"] = snapshot.get("club_name")
                player_result["club_recent_matches"] = snapshot.get("club_recent_matches")
                player_result["national_recent_caps"] = snapshot.get("national_recent_caps")
                player_result["missing_fields"] = player_record.get("missing_fields", [])
                team_diagnostic["mapped_players"] += 1
                team_diagnostic["snapshots_built"] += 1
                team_diagnostic["player_results"].append(player_result)

            if team_diagnostic["snapshots_built"] == 0:
                team_diagnostic["status"] = "no_player_form_snapshots"
            elif team_diagnostic["snapshots_built"] < min(target_players, len(players)):
                team_diagnostic["status"] = "partial"
            diagnostics.append(team_diagnostic)

    snapshot_rows = _sort_player_form_rows(snapshot_rows)
    snapshot_rows = _cap_player_form_rows(
        snapshot_rows,
        core_player_rows=core_player_rows,
        max_players_per_team=max_players_per_team,
        max_players_by_team=team_player_targets,
    )
    core_player_rows.sort(key=lambda item: (item["team_id"], int(item["selection_rank"] or "999")))
    write_csv(output_dir / "core_players.csv", CORE_PLAYER_FIELDNAMES, core_player_rows)
    write_json(output_dir / "player_form_snapshots.json", snapshot_rows)
    _write_player_url_cache(output_dir, player_url_cache, accessed_at=accessed_at)
    diagnostics_payload = {
        "accessed_at": accessed_at,
        "recent_club_match_limit": recent_club_match_limit,
        "max_players_per_team": max_players_per_team,
        "min_players_per_team": DEFAULT_MIN_PLAYERS_PER_TEAM,
        "page_timeout_ms": page_timeout_ms,
        "selected_teams": len(selected_team_ids) if selected_only else len(target_team_rows),
        "target_teams": len(target_team_rows),
        "rows_written": len(snapshot_rows),
        "preserve_existing": preserve_existing,
        "selected_team_filter": sorted(only_team_ids) if only_team_ids else [],
        "team_player_targets": team_player_targets,
        "player_url_cache_entries": len(player_url_cache),
        "diagnostics": diagnostics,
    }
    diagnostics_payload = _merge_player_form_diagnostics_payload(
        output_dir,
        diagnostics_payload,
        preserve_existing=preserve_existing,
    )
    write_json(output_dir / "player_form_diagnostics.json", diagnostics_payload)
    _update_manifest(
        output_dir / "source_manifest.json",
        team_player_targets=team_player_targets,
        player_url_cache_entries=len(player_url_cache),
    )
    return diagnostics_payload


def build_player_form_snapshot(
    *,
    player_row: dict[str, str],
    team_row: dict[str, str],
    player_record: dict[str, Any],
    player_html: str,
    accessed_at: str,
) -> dict[str, Any]:
    club_name = str(player_record.get("player", {}).get("current_team") or "").strip()
    club_recent_matches = list(player_record.get("recent_club_matches") or [])
    national_recent_matches = [
        row
        for row in list(player_record.get("all_recent_matches") or [])
        if _match_has_team(row, team_row)
    ]
    club_source_id = _current_team_source_id(player_html, club_name)
    return {
        "snapshot_id": f"player_form_{player_row['player_id']}",
        "player_id": player_row["player_id"],
        "team_id": player_row["team_id"],
        "club_name": club_name,
        "club_source_id": club_source_id,
        "as_of": accessed_at,
        "club_recent_matches": len(club_recent_matches),
        "club_recent_starts": _proxy_starts(club_recent_matches),
        "club_recent_minutes": _sum_minutes(club_recent_matches),
        "club_recent_goals": None,
        "club_recent_assists": None,
        "national_recent_caps": len(national_recent_matches),
        "national_recent_starts": _proxy_starts(national_recent_matches),
        "national_recent_minutes": _sum_minutes(national_recent_matches),
        "national_recent_goals": None,
        "national_recent_assists": None,
        "source": PLAYER_FORM_SOURCE,
        "source_player_id": player_row["source_player_id"],
        "available_at": accessed_at,
    }


def _core_players_by_team(
    core_player_rows: list[dict[str, str]],
    *,
    max_players_per_team: int,
    selected_team_ids: set[str],
) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in core_player_rows:
        team_id = row["team_id"]
        if team_id not in selected_team_ids:
            continue
        grouped.setdefault(team_id, []).append(row)
    for team_id in grouped:
        grouped[team_id] = sorted(
            grouped[team_id],
            key=lambda item: int(item.get("selection_rank") or "999"),
        )
    return grouped


def player_target_count_for_rank(
    rank: float | None,
    *,
    max_players_per_team: int,
    min_players_per_team: int = DEFAULT_MIN_PLAYERS_PER_TEAM,
) -> int:
    upper = max(int(max_players_per_team), 1)
    lower = min(max(int(min_players_per_team), 1), upper)
    if rank is None:
        return lower
    if rank <= 20:
        return upper
    if rank <= 40:
        return min(upper, 6)
    if rank <= 80:
        return min(upper, 5)
    return lower


def build_team_player_target_counts(
    team_rows: list[dict[str, str]],
    strength_rows: list[dict[str, Any]],
    *,
    max_players_per_team: int,
    min_players_per_team: int = DEFAULT_MIN_PLAYERS_PER_TEAM,
) -> dict[str, int]:
    rank_by_team = {
        str(row.get("team_id") or ""): _coerce_float(row.get("strength_value"))
        for row in strength_rows
        if str(row.get("strength_type") or "") == "fifa_world_ranking_position"
    }
    return {
        str(team_row["team_id"]): player_target_count_for_rank(
            rank_by_team.get(str(team_row["team_id"])),
            max_players_per_team=max_players_per_team,
            min_players_per_team=min_players_per_team,
        )
        for team_row in team_rows
        if team_row.get("team_id")
    }


async def _team_record_for_row(
    *,
    workflow: Any,
    workflow_module: Any,
    team_row: dict[str, str],
    recent_club_match_limit: int,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    aliases = [alias.strip() for alias in str(team_row.get("aliases") or "").split("|") if alias.strip()]
    selected_link = None
    selected_query = team_row["canonical_name"]
    search_links: list[dict[str, str]] = []
    try:
        for query in build_search_queries(team_row["canonical_name"], aliases):
            search_html = await workflow.fetch_html(search_url(query))
            search_links = extract_search_team_links(search_html)
            selected_link = select_best_team_link(team_row["canonical_name"], [*aliases, query], search_links)
            if selected_link is not None:
                selected_query = query
                break
    except Exception as exc:  # noqa: BLE001
        return None, None, str(exc)
    if selected_link is None:
        return None, None, f"search_failed:{selected_query}:{len(search_links)}"
    team_url = _absolute_whoscored_url(selected_link["url"])
    try:
        team_html = await workflow.fetch_html(team_url)
        try:
            team_record = workflow_module.parse_team_page(
                html=team_html,
                page_url=team_url,
                recent_match_limit=max(1, recent_club_match_limit),
            )
        except Exception:
            team_record = {
                "source": "whoscored",
                "record_type": "national_team_recent_matches",
                "page_url": team_url,
                "team": team_row["canonical_name"],
                "recent_matches": [],
                "candidate_player_links": _extract_player_links(team_html),
                "status": "partial",
                "missing_fields": ["recent_matches_not_found"],
            }
    except Exception as exc:  # noqa: BLE001
        return team_url, None, str(exc)
    return team_url, team_record, None


async def _resolve_player_link(
    *,
    workflow: Any,
    player_row: dict[str, str],
    team_row: dict[str, str],
    candidate_links: list[dict[str, str]],
    player_url_cache: dict[str, dict[str, str]],
) -> tuple[dict[str, str] | None, dict[str, Any]]:
    cached = _cached_player_link(player_row, player_url_cache)
    if cached is not None:
        return cached, {
            "player_link_source": "player_url_cache",
            "search_query": "",
            "search_result_count": 0,
        }
    selected_candidate = select_best_player_link(player_row["canonical_name"], candidate_links)
    if selected_candidate is not None:
        return (
            {
                **selected_candidate,
                "url": _player_fixtures_url(selected_candidate["url"]),
            },
            {
                "player_link_source": "team_page_candidate",
                "search_query": "",
                "search_result_count": len(candidate_links),
            },
        )
    last_error = ""
    last_query = ""
    last_link_count = 0
    for query in _build_player_search_queries(player_row["canonical_name"], team_row):
        last_query = query
        try:
            search_html = await workflow.fetch_html(search_url(query))
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            continue
        search_links = _extract_player_links(search_html)
        last_link_count = len(search_links)
        selected_link = select_best_player_link(player_row["canonical_name"], search_links)
        if selected_link is not None:
            return (
                {
                    **selected_link,
                    "url": _player_fixtures_url(selected_link["url"]),
                },
                {
                    "player_link_source": "search",
                    "search_query": query,
                    "search_result_count": len(search_links),
                },
            )
    direct_url = _direct_player_url_alias(player_row["canonical_name"])
    if direct_url:
        return (
            {
                "label": player_row["canonical_name"],
                "url": direct_url,
            },
            {
                "player_link_source": "direct_url_alias",
                "search_query": last_query,
                "search_result_count": last_link_count,
                "search_error": last_error,
            },
        )
    return None, {
        "player_link_source": "search",
        "search_query": last_query,
        "search_result_count": last_link_count,
        "search_error": last_error,
    }


def _load_existing_player_form_rows(output_dir: Path, *, preserve_existing: bool) -> list[dict[str, Any]]:
    path = output_dir / "player_form_snapshots.json"
    if not preserve_existing or not path.exists():
        return []
    payload = read_json(path)
    return [item for item in payload if isinstance(item, dict)]


def _sort_player_form_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("player_id") or ""), str(row.get("as_of") or ""))
        if key[0] and key[1]:
            deduped[key] = row
    return sorted(deduped.values(), key=lambda item: (item["team_id"], item["player_id"]))


def _cap_player_form_rows(
    rows: list[dict[str, Any]],
    *,
    core_player_rows: list[dict[str, str]],
    max_players_per_team: int,
    max_players_by_team: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    rank_by_player = {
        row["player_id"]: int(row.get("selection_rank") or "999")
        for row in core_player_rows
        if row.get("player_id")
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        team_id = str(row.get("team_id") or "")
        as_of = str(row.get("as_of") or "")
        if not team_id or not as_of:
            continue
        grouped.setdefault((team_id, as_of), []).append(row)

    capped: list[dict[str, Any]] = []
    for _, group_rows in grouped.items():
        team_id = str(group_rows[0].get("team_id") or "")
        effective_max = (
            int(max_players_by_team.get(team_id, max_players_per_team))
            if max_players_by_team is not None
            else int(max_players_per_team)
        )
        group_rows.sort(
            key=lambda item: (
                rank_by_player.get(str(item.get("player_id") or ""), 999),
                str(item.get("player_id") or ""),
            )
        )
        capped.extend(group_rows[:effective_max])
    return sorted(capped, key=lambda item: (item["team_id"], item["player_id"]))


def _update_core_player_row(player_row: dict[str, str], snapshot: dict[str, Any]) -> None:
    player_row["club_name"] = str(snapshot.get("club_name") or "")
    player_row["club_source_id"] = str(snapshot.get("club_source_id") or "")


def _load_player_url_cache(output_dir: Path) -> dict[str, dict[str, str]]:
    path = output_dir / PLAYER_URL_CACHE_FILE
    if not path.exists():
        return {}
    payload = read_json(path)
    players = payload.get("players") if isinstance(payload, dict) else payload
    if not isinstance(players, dict):
        return {}
    return {
        str(player_id): {
            key: str(value)
            for key, value in entry.items()
            if value is not None
        }
        for player_id, entry in players.items()
        if isinstance(entry, dict) and str(entry.get("player_url") or "").strip()
    }


def _write_player_url_cache(
    output_dir: Path,
    player_url_cache: dict[str, dict[str, str]],
    *,
    accessed_at: str,
) -> None:
    payload = {
        "updated_at": accessed_at,
        "players": {
            player_id: player_url_cache[player_id]
            for player_id in sorted(player_url_cache)
        },
    }
    write_json(output_dir / PLAYER_URL_CACHE_FILE, payload)


def _cached_player_link(
    player_row: dict[str, str],
    player_url_cache: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    entry = player_url_cache.get(str(player_row.get("player_id") or ""))
    if entry is None:
        return None
    player_url = str(entry.get("player_url") or "").strip()
    if not player_url:
        return None
    return {
        "label": str(entry.get("label") or player_row.get("canonical_name") or ""),
        "url": player_url,
    }


def _update_player_url_cache(
    player_url_cache: dict[str, dict[str, str]],
    *,
    player_row: dict[str, str],
    selected_link: dict[str, str],
    resolution: dict[str, Any],
    accessed_at: str,
) -> None:
    player_id = str(player_row.get("player_id") or "")
    if not player_id:
        return
    player_url_cache[player_id] = {
        "player_id": player_id,
        "canonical_name": str(player_row.get("canonical_name") or ""),
        "team_id": str(player_row.get("team_id") or ""),
        "label": str(selected_link.get("label") or player_row.get("canonical_name") or ""),
        "player_url": str(selected_link.get("url") or ""),
        "player_link_source": str(resolution.get("player_link_source") or ""),
        "updated_at": accessed_at,
    }


def _merge_player_form_diagnostics_payload(
    output_dir: Path,
    diagnostics_payload: dict[str, Any],
    *,
    preserve_existing: bool,
) -> dict[str, Any]:
    path = output_dir / "player_form_diagnostics.json"
    if not preserve_existing or not path.exists():
        return diagnostics_payload
    existing = read_json(path)
    if not isinstance(existing, dict):
        return diagnostics_payload
    merged = dict(existing)
    merged.update({
        "accessed_at": existing.get("accessed_at") or diagnostics_payload["accessed_at"],
        "latest_run_accessed_at": diagnostics_payload["accessed_at"],
        "recent_club_match_limit": diagnostics_payload["recent_club_match_limit"],
        "max_players_per_team": diagnostics_payload["max_players_per_team"],
        "min_players_per_team": diagnostics_payload["min_players_per_team"],
        "page_timeout_ms": diagnostics_payload["page_timeout_ms"],
        "target_teams": max(
            int(existing.get("target_teams") or 0),
            int(diagnostics_payload.get("target_teams") or 0),
        ),
        "rows_written": diagnostics_payload["rows_written"],
        "preserve_existing": True,
        "team_player_targets": {
            **dict(existing.get("team_player_targets") or {}),
            **dict(diagnostics_payload.get("team_player_targets") or {}),
        },
        "player_url_cache_entries": diagnostics_payload["player_url_cache_entries"],
    })
    merged_filters = {
        *[str(item) for item in existing.get("selected_team_filter") or []],
        *[str(item) for item in diagnostics_payload.get("selected_team_filter") or []],
    }
    merged["selected_team_filter"] = sorted(item for item in merged_filters if item)
    diagnostics_by_team = {
        str(item.get("team_id") or ""): item
        for item in existing.get("diagnostics") or []
        if isinstance(item, dict) and item.get("team_id")
    }
    for item in diagnostics_payload.get("diagnostics") or []:
        if isinstance(item, dict) and item.get("team_id"):
            diagnostics_by_team[str(item["team_id"])] = item
    merged["diagnostics"] = [
        diagnostics_by_team[team_id]
        for team_id in sorted(diagnostics_by_team)
    ]
    merged["selected_teams"] = len(merged["diagnostics"])
    return merged


def _match_has_team(match: dict[str, Any], team_row: dict[str, str]) -> bool:
    team_keys = _team_keys(team_row)
    return _normalize_name(match.get("home_team")) in team_keys or _normalize_name(match.get("away_team")) in team_keys


def _team_keys(team_row: dict[str, str]) -> set[str]:
    keys = {_normalize_name(team_row.get("canonical_name"))}
    for alias in str(team_row.get("aliases") or "").split("|"):
        normalized = _normalize_name(alias)
        if normalized:
            keys.add(normalized)
    keys.update(_normalize_name(alias) for alias in TEAM_SEARCH_QUERY_ALIASES.get(_normalize_name(team_row.get("canonical_name")), ()))
    return {item for item in keys if item}


def _proxy_starts(matches: list[dict[str, Any]]) -> int:
    return sum(1 for row in matches if isinstance(row.get("minutes"), int) and int(row["minutes"]) >= 60)


def _sum_minutes(matches: list[dict[str, Any]]) -> int:
    return sum(int(row["minutes"]) for row in matches if isinstance(row.get("minutes"), int))


def _current_team_source_id(player_html: str, club_name: str) -> str:
    match = re.search(r'Current Team:\s*</dt>\s*<dd[^>]*>\s*<a[^>]+href=\"([^\"]+)\"', player_html, re.I)
    if match:
        return _absolute_whoscored_url(match.group(1))
    return f"whoscored_club_name:{_slug(club_name)}" if club_name else ""


def _build_player_search_queries(player_name: str, team_row: dict[str, str]) -> list[str]:
    del team_row
    queries = [player_name, _space_normalize_query(player_name)]
    ascii_name = _ascii_fold(player_name)
    if ascii_name:
        queries.append(ascii_name)
        queries.append(_space_normalize_query(ascii_name))
    queries.extend(PLAYER_SEARCH_QUERY_ALIASES.get(_normalize_name(player_name), ()))
    return _dedupe_queries(queries)


def _dedupe_queries(queries: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for query in queries:
        normalized = query.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _extract_player_links(html: str) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    links = []
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


def _player_fixtures_url(url: str) -> str:
    return re.sub(r"/show/", "/fixtures/", _absolute_whoscored_url(url), count=1)


def _absolute_whoscored_url(url: str) -> str:
    return url if url.startswith("http") else f"https://www.whoscored.com{url}"


def _ascii_fold(value: Any) -> str:
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")


def _space_normalize_query(value: Any) -> str:
    return " ".join(re.sub(r"[-_/]+", " ", str(value or "")).split())


def _direct_player_url_alias(player_name: str) -> str | None:
    return PLAYER_DIRECT_URL_ALIASES.get(_normalize_name(player_name))


def _normalize_name(value: Any) -> str:
    ascii_value = _ascii_fold(value).casefold()
    cleaned = re.sub(r"[^a-z0-9]+", " ", ascii_value)
    return " ".join(cleaned.split())


def _token_similarity(left: list[str], right: list[str]) -> int:
    if not left or not right:
        return 0
    left_set = set(left)
    right_set = set(right)
    common = len(left_set & right_set)
    total = max(len(left_set | right_set), 1)
    return int((common / total) * 100)


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "_" for char in str(value or "").strip())
    return "_".join(part for part in slug.split("_") if part)


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _team_strength_rank(team_id: str, strength_rows: list[dict[str, Any]]) -> float | None:
    for row in strength_rows:
        if str(row.get("team_id") or "") != team_id:
            continue
        if str(row.get("strength_type") or "") != "fifa_world_ranking_position":
            continue
        return _coerce_float(row.get("strength_value"))
    return None


def _load_workflow_module(skill_scripts_dir: Path):
    scripts_dir = str(skill_scripts_dir)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import whoscored_workflow

    return whoscored_workflow


def _update_manifest(
    path: Path,
    *,
    team_player_targets: dict[str, int],
    player_url_cache_entries: int,
) -> None:
    manifest = read_json(path)
    manifest["player_form_file"] = "player_form_snapshots.json"
    manifest["player_form_diagnostics_file"] = "player_form_diagnostics.json"
    manifest["player_url_cache_file"] = PLAYER_URL_CACHE_FILE
    manifest["player_form_target_strategy"] = "fifa_rank_tiered_dynamic_targets"
    manifest["player_form_target_counts"] = {
        team_id: int(target)
        for team_id, target in sorted(team_player_targets.items())
    }
    coverage = manifest.get("coverage_summary") if isinstance(manifest.get("coverage_summary"), dict) else {}
    coverage["player_form_snapshots"] = len(read_json(path.parent / "player_form_snapshots.json"))
    coverage["player_url_cache_entries"] = player_url_cache_entries
    manifest["coverage_summary"] = coverage
    schema_gaps = manifest.get("schema_gaps") if isinstance(manifest.get("schema_gaps"), dict) else {}
    schema_gaps["player_form_snapshots_pending"] = False
    manifest["schema_gaps"] = schema_gaps
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


async def _async_main(args: argparse.Namespace) -> None:
    result = await enrich_player_form(
        output_dir=args.output_dir,
        accessed_at=args.accessed_at,
        recent_club_match_limit=args.recent_club_match_limit,
        max_players_per_team=args.max_players_per_team,
        page_timeout_ms=args.page_timeout_ms,
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
    parser.add_argument("--recent-club-match-limit", type=int, default=DEFAULT_RECENT_CLUB_MATCH_LIMIT)
    parser.add_argument("--max-players-per-team", type=int, default=DEFAULT_MAX_PLAYERS_PER_TEAM)
    parser.add_argument("--page-timeout-ms", type=int, default=DEFAULT_PLAYER_PAGE_TIMEOUT_MS)
    parser.add_argument("--include-support-teams", action="store_true")
    parser.add_argument("--skill-scripts-dir", type=Path, default=DEFAULT_SKILL_SCRIPTS_DIR)
    parser.add_argument("--team-id", action="append", default=[])
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()

    import asyncio

    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
