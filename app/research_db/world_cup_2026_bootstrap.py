from __future__ import annotations

import argparse
import csv
import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests

from app.config import ROOT
from app.world_cup_2026_schedule import load_frozen_schedule_rows

from .repository import ResearchDatabaseRepository


TEAMS_MODULE_URL = "https://cxm-api.fifa.com/fifaplusweb/api/sections/teamsModule/4v5Yng3VdGD9c1cpnOIff1?locale=en&limit=200"
FIXTURES_URL = "https://api.fifa.com/api/v3/calendar/matches?language=en&count=500&idSeason=285023"
FANTASY_SQUADS_URL = "https://play.fifa.com/json/fantasy/squads.json"
FANTASY_PLAYERS_URL = "https://play.fifa.com/json/fantasy/players.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "research_import" / "p0_11"
DEFAULT_DB_PATH = ROOT / "outputs" / "research_p0_11_wc2026.db"
SOURCE_NAME = "fifa_world_cup_2026_bootstrap"
TEAM_STRENGTH_SOURCE = "fifa_teams_module"
CORE_PLAYER_SOURCE = "fifa_fantasy"
DEFAULT_TOP_PLAYERS_PER_TEAM = 12
COMPETITION_NAME = "2026 FIFA World Cup"
TEAM_FIELDNAMES = (
    "team_id",
    "canonical_name",
    "country_code",
    "fifa_code",
    "source_team_id",
    "confederation",
    "stage",
    "host_team",
    "available_at",
    "aliases",
)
FIXTURE_FIELDNAMES = (
    "match_no",
    "fixture_id",
    "match_time",
    "local_match_time",
    "home_team_id",
    "away_team_id",
    "home_fifa_code",
    "away_fifa_code",
    "result_status",
    "competition",
    "season",
    "stage",
    "group_name",
    "neutral_field",
    "stadium",
    "city",
    "fixture_available_at",
    "source_fixture_id",
)
CORE_PLAYER_FIELDNAMES = (
    "player_id",
    "canonical_name",
    "team_id",
    "nationality",
    "position",
    "club_name",
    "club_source_id",
    "source_player_id",
    "fifa_player_id",
    "squad_role",
    "shirt_number",
    "selection_rank",
    "selection_reason",
    "available_at",
)
NATIONAL_RECENT_RESULT_FIELDNAMES = (
    "fixture_id",
    "competition",
    "season",
    "match_time",
    "home_team_id",
    "away_team_id",
    "neutral_field",
    "result_status",
    "home_score",
    "away_score",
    "opponent_team_id",
    "source_fixture_id",
    "source_result_id",
    "available_at",
)
DEFAULT_HEADERS = {"user-agent": "Mozilla/5.0"}
POSITION_MAP = {
    "GK": "GK",
    "DEF": "DF",
    "MID": "MF",
    "FWD": "FW",
}


def fetch_json(url: str) -> Any:
    response = requests.get(url, timeout=60, headers=DEFAULT_HEADERS)
    response.raise_for_status()
    return response.json()


def build_bootstrap_bundle(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    accessed_at: str,
    top_players_per_team: int = DEFAULT_TOP_PLAYERS_PER_TEAM,
) -> dict[str, Any]:
    teams_module = fetch_json(TEAMS_MODULE_URL)
    fixtures_payload = fetch_json(FIXTURES_URL)
    fantasy_squads = fetch_json(FANTASY_SQUADS_URL)
    fantasy_players = fetch_json(FANTASY_PLAYERS_URL)

    team_rows, team_strength_rows = build_team_rows(teams_module, accessed_at=accessed_at)
    fixture_rows = build_fixture_rows(fixtures_payload, team_rows=team_rows, accessed_at=accessed_at)
    core_player_rows = build_core_player_rows(
        fantasy_squads,
        fantasy_players,
        team_rows=team_rows,
        accessed_at=accessed_at,
        top_players_per_team=top_players_per_team,
    )
    manifest = build_source_manifest(
        accessed_at=accessed_at,
        team_rows=team_rows,
        fixture_rows=fixture_rows,
        core_player_rows=core_player_rows,
        top_players_per_team=top_players_per_team,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "world_cup_2026_teams.csv", TEAM_FIELDNAMES, team_rows)
    write_csv(output_dir / "world_cup_2026_fixtures.csv", FIXTURE_FIELDNAMES, fixture_rows)
    write_csv(output_dir / "core_players.csv", CORE_PLAYER_FIELDNAMES, core_player_rows)
    write_csv(output_dir / "national_recent_results.csv", NATIONAL_RECENT_RESULT_FIELDNAMES, [])
    write_json(output_dir / "player_form_snapshots.json", [])
    write_json(output_dir / "odds_snapshots.json", [])
    write_json(output_dir / "team_strength_snapshots.json", team_strength_rows)
    write_json(output_dir / "source_manifest.json", manifest)

    return {
        "status": "ok",
        "output_dir": str(output_dir),
        "counts": {
            "teams": len(team_rows),
            "fixtures": len(fixture_rows),
            "core_players": len(core_player_rows),
            "team_strength_snapshots": len(team_strength_rows),
        },
        "manifest_path": str(output_dir / "source_manifest.json"),
    }


def build_team_rows(teams_module: dict[str, Any], *, accessed_at: str) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    rows: list[dict[str, str]] = []
    strength_rows: list[dict[str, Any]] = []
    for team in teams_module.get("teams", []):
        if not isinstance(team, dict):
            continue
        canonical_name = str(team.get("teamName") or "").strip()
        fifa_code = _extract_fifa_code(str(team.get("teamFlag") or ""))
        if not canonical_name or not fifa_code:
            continue
        team_id = f"team_{_slug(canonical_name)}"
        rows.append({
            "team_id": team_id,
            "canonical_name": canonical_name,
            "country_code": fifa_code,
            "fifa_code": fifa_code,
            "source_team_id": str(team.get("teamId") or fifa_code),
            "confederation": str(team.get("confederationId") or ""),
            "stage": str(team.get("stage") or ""),
            "host_team": "true" if bool(team.get("hostTeam")) else "false",
            "available_at": accessed_at,
            "aliases": "|".join(_team_aliases(canonical_name, fifa_code)),
        })
        if team.get("worldRanking") is not None:
            strength_rows.append({
                "snapshot_id": f"team_strength_{team_id}_{accessed_at[:10]}_fifa_world_ranking_position",
                "team_id": team_id,
                "strength_type": "fifa_world_ranking_position",
                "strength_value": float(team["worldRanking"]),
                "strength_source": TEAM_STRENGTH_SOURCE,
                "source": TEAM_STRENGTH_SOURCE,
                "source_team_id": str(team.get("teamId") or fifa_code),
                "as_of": accessed_at,
                "available_at": accessed_at,
            })
    return rows, strength_rows


def build_fixture_rows(
    fixtures_payload: dict[str, Any],
    *,
    team_rows: list[dict[str, str]],
    accessed_at: str,
) -> list[dict[str, str]]:
    by_team_source = {
        str(team["source_team_id"]): team["team_id"]
        for team in team_rows
    }
    rows: list[dict[str, str]] = []
    results = fixtures_payload.get("Results") if isinstance(fixtures_payload, dict) else []
    for match in results or []:
        if not isinstance(match, dict):
            continue
        home = match.get("Home") if isinstance(match.get("Home"), dict) else {}
        away = match.get("Away") if isinstance(match.get("Away"), dict) else {}
        home_source = str(home.get("IdTeam") or "")
        away_source = str(away.get("IdTeam") or "")
        home_team_id = by_team_source.get(home_source)
        away_team_id = by_team_source.get(away_source)
        if not home_team_id or not away_team_id:
            continue
        stage_name = _localized(match.get("StageName"))
        group_name = _localized(match.get("GroupName"))
        stadium = match.get("Stadium") if isinstance(match.get("Stadium"), dict) else {}
        rows.append({
            "match_no": str(match.get("MatchNumber") or ""),
            "fixture_id": f"fixture_wc2026_{match['IdMatch']}",
            "match_time": str(match.get("Date") or ""),
            "local_match_time": str(match.get("LocalDate") or ""),
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_fifa_code": str(home.get("IdCountry") or home.get("Abbreviation") or ""),
            "away_fifa_code": str(away.get("IdCountry") or away.get("Abbreviation") or ""),
            "result_status": "scheduled" if match.get("MatchStatus") == 1 else "unknown",
            "competition": COMPETITION_NAME,
            "season": "2026",
            "stage": stage_name,
            "group_name": group_name,
            "neutral_field": "true",
            "stadium": _localized(stadium.get("Name")) or "",
            "city": _localized(stadium.get("CityName")) or "",
            "fixture_available_at": accessed_at,
            "source_fixture_id": str(match.get("IdMatch") or ""),
        })
    return rows


def build_core_player_rows(
    fantasy_squads: list[dict[str, Any]],
    fantasy_players: list[dict[str, Any]],
    *,
    team_rows: list[dict[str, str]],
    accessed_at: str,
    top_players_per_team: int = DEFAULT_TOP_PLAYERS_PER_TEAM,
) -> list[dict[str, str]]:
    by_team_name = {
        _normalize_key(team["canonical_name"]): team
        for team in team_rows
    }
    by_team_code = {
        _normalize_key(team["fifa_code"]): team
        for team in team_rows
    }
    squad_to_team: dict[int, dict[str, str]] = {}
    for squad in fantasy_squads:
        if not isinstance(squad, dict):
            continue
        team = by_team_name.get(_normalize_key(str(squad.get("name") or "")))
        if team is None:
            team = by_team_code.get(_normalize_key(str(squad.get("abbr") or "")))
        if team is not None:
            squad_to_team[int(squad["id"])] = team

    grouped_players: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in fantasy_players:
        if not isinstance(player, dict):
            continue
        team = squad_to_team.get(int(player.get("squadId") or -1))
        if team is None:
            continue
        if str(player.get("status") or "").lower() == "transferred":
            continue
        grouped_players[team["team_id"]].append(player)

    rows: list[dict[str, str]] = []
    for team_id, players in grouped_players.items():
        team = next(item for item in team_rows if item["team_id"] == team_id)
        ranked = sorted(
            players,
            key=lambda item: (
                -float(item.get("percentSelected") or 0.0),
                -float(item.get("price") or 0.0),
                int(item.get("id") or 0),
            ),
        )[:top_players_per_team]
        for index, player in enumerate(ranked, start=1):
            name = _player_display_name(player)
            rows.append({
                "player_id": f"player_{_slug(team['fifa_code'])}_{player['id']}",
                "canonical_name": name,
                "team_id": team_id,
                "nationality": team["country_code"],
                "position": POSITION_MAP.get(str(player.get("position") or "").upper(), str(player.get("position") or "")),
                "club_name": "",
                "club_source_id": "",
                "source_player_id": f"fantasy_player_{player['id']}",
                "fifa_player_id": "",
                "squad_role": "core_shortlist",
                "shirt_number": "",
                "selection_rank": str(index),
                "selection_reason": "fifa_fantasy_percent_selected_then_price",
                "available_at": accessed_at,
            })
    return sorted(rows, key=lambda item: (item["team_id"], int(item["selection_rank"])))


def build_source_manifest(
    *,
    accessed_at: str,
    team_rows: list[dict[str, str]],
    fixture_rows: list[dict[str, str]],
    core_player_rows: list[dict[str, str]],
    top_players_per_team: int,
) -> dict[str, Any]:
    selected_team_ids = [team["team_id"] for team in team_rows]
    default_fixture_ids = [row["fixture_id"] for row in fixture_rows[:12]]
    return {
        "dataset_id": "p0_11_world_cup_2026_bootstrap",
        "objective": "Phase-0 bootstrap for FIFA World Cup 2026 team scope, fixture scope, core-player shortlist, and baseline team-strength snapshots.",
        "source": SOURCE_NAME,
        "competition": COMPETITION_NAME,
        "season": "2026",
        "coverage_mode": "official_scope_bootstrap",
        "selected_team_ids": selected_team_ids,
        "teams_file": "world_cup_2026_teams.csv",
        "fixtures_file": "world_cup_2026_fixtures.csv",
        "matches_file": "national_recent_results.csv",
        "players_file": "core_players.csv",
        "player_form_file": "player_form_snapshots.json",
        "odds_file": "odds_snapshots.json",
        "odds_diagnostics_file": "odds_diagnostics.json",
        "team_strength_file": "team_strength_snapshots.json",
        "accessed_at": accessed_at,
        "source_policy": {
            "strategy": "official_fifa_bootstrap_plus_fantasy_core_shortlist",
            "raw_saved": False,
            "network_required_for_import": False,
            "notes": [
                "This bootstrap bundle fixes official team and fixture scope before live recent-results and player-form enrichment.",
                "Core players are a shortlist derived from FIFA fantasy selection signals, not yet a full recent-form ingest.",
                "Team strength snapshots currently use official current world-ranking position with explicit source typing.",
            ],
        },
        "source_urls": [
            "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/teams",
            "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures",
            TEAMS_MODULE_URL,
            FIXTURES_URL,
            FANTASY_SQUADS_URL,
            FANTASY_PLAYERS_URL,
        ],
        "default_snapshot_fixture_ids": default_fixture_ids,
        "core_player_shortlist_size": top_players_per_team,
        "schema_gaps": {
            "national_recent_results_pending": True,
            "player_form_snapshots_pending": True,
            "odds_snapshots_pending": True,
            "club_mapping_pending": True,
        },
        "coverage_summary": {
            "teams": len(team_rows),
            "fixtures": len(fixture_rows),
            "core_players": len(core_player_rows),
            "odds_snapshots": 0,
        },
    }


def load_bootstrap_records(base_dir: Path) -> dict[str, Any]:
    teams = read_csv(base_dir / "world_cup_2026_teams.csv")
    fixtures = load_frozen_schedule_rows(base_dir / "world_cup_2026_fixtures.csv")
    national_recent_results = read_csv(base_dir / "national_recent_results.csv")
    core_players = read_csv(base_dir / "core_players.csv")
    player_form_snapshots = read_json(base_dir / "player_form_snapshots.json")
    team_strength_snapshots = read_json(base_dir / "team_strength_snapshots.json")
    return {
        "teams": teams,
        "fixtures": fixtures,
        "national_recent_results": national_recent_results,
        "core_players": core_players,
        "player_form_snapshots": player_form_snapshots,
        "team_strength_snapshots": team_strength_snapshots,
    }


def build_bootstrap_import_payload(base_dir: Path) -> dict[str, Any]:
    records = load_bootstrap_records(base_dir)
    facts: dict[str, list[dict[str, Any]]] = {
        "teams": [],
        "players": [],
        "fixtures": [],
        "match_results": [],
        "squads": [],
        "player_stats": [],
        "player_form_snapshots": [],
        "team_strength_snapshots": [],
        "team_aliases": [],
    }

    for row in records["teams"]:
        facts["teams"].append({
            "team_id": row["team_id"],
            "canonical_name": row["canonical_name"],
            "country_code": row["country_code"],
            "fifa_code": row["fifa_code"],
            "source": SOURCE_NAME,
            "source_team_id": row["source_team_id"],
            "available_at": row["available_at"],
        })
        for alias in str(row.get("aliases") or "").split("|"):
            cleaned = alias.strip()
            if not cleaned:
                continue
            facts["team_aliases"].append({
                "alias_id": f"alias_{row['team_id']}_{_slug(cleaned)}",
                "entity_type": "team",
                "entity_id": row["team_id"],
                "alias": cleaned,
                "source": SOURCE_NAME,
                "source_id": row["source_team_id"] if cleaned == row["fifa_code"] else f"{row['source_team_id']}:{_slug(cleaned)}",
                "confidence": 1.0,
                "available_at": row["available_at"],
            })

    competition_id = "competition_fifa_world_cup_2026"
    competition_available_at = facts["teams"][0]["available_at"] if facts["teams"] else ""
    for alias_id, alias in (
        ("alias_competition_fifa_world_cup_2026", COMPETITION_NAME),
        ("alias_competition_fifa_world_cup_2026_legacy", "FIFA World Cup 2026"),
        ("alias_competition_fifa_world_cup_2026_tm", "FIFA World Cup 2026™"),
    ):
        facts["team_aliases"].append({
            "alias_id": alias_id,
            "entity_type": "competition",
            "entity_id": competition_id,
            "alias": alias,
            "source": SOURCE_NAME,
            "source_id": "competition_fifa_world_cup_2026",
            "confidence": 1.0,
            "available_at": competition_available_at,
        })

    for row in records["fixtures"]:
        facts["fixtures"].append({
            "fixture_id": row["fixture_id"],
            "competition": row["competition"],
            "season": row["season"],
            "home_team_id": row["home_team_id"],
            "away_team_id": row["away_team_id"],
            "match_time": row["match_time"],
            "neutral_field": str(row.get("neutral_field") or "").lower() == "true",
            "source": SOURCE_NAME,
            "source_fixture_id": row["source_fixture_id"],
            "available_at": row["fixture_available_at"],
        })

    for row in records["national_recent_results"]:
        facts["fixtures"].append({
            "fixture_id": row["fixture_id"],
            "competition": row["competition"],
            "season": row["season"],
            "home_team_id": row["home_team_id"],
            "away_team_id": row["away_team_id"],
            "match_time": row["match_time"],
            "neutral_field": _bool_from_text(row.get("neutral_field")),
            "source": SOURCE_NAME,
            "source_fixture_id": row["source_fixture_id"],
            "available_at": row["available_at"],
        })
        facts["match_results"].append({
            "result_id": row["source_result_id"],
            "fixture_id": row["fixture_id"],
            "home_score": int(row["home_score"]),
            "away_score": int(row["away_score"]),
            "result_status": row["result_status"],
            "played_at": row["match_time"],
            "available_at": row["available_at"],
            "source": SOURCE_NAME,
            "source_result_id": row["source_result_id"],
        })

    for row in records["core_players"]:
        facts["players"].append({
            "player_id": row["player_id"],
            "canonical_name": row["canonical_name"],
            "team_id": row["team_id"],
            "nationality": row["nationality"],
            "position": row["position"],
            "club": row.get("club_name") or None,
            "source": CORE_PLAYER_SOURCE,
            "source_player_id": row["source_player_id"],
            "available_at": row["available_at"],
        })
        facts["squads"].append({
            "squad_id": f"squad_{row['player_id']}_wc2026_core_shortlist",
            "team_id": row["team_id"],
            "competition": COMPETITION_NAME,
            "season": "2026",
            "player_id": row["player_id"],
            "role": row["squad_role"],
            "shirt_number": int(row["shirt_number"]) if str(row.get("shirt_number") or "").strip() else None,
            "source": CORE_PLAYER_SOURCE,
            "source_squad_id": f"{row['source_player_id']}:wc2026_core_shortlist",
            "available_at": row["available_at"],
        })

    facts["player_form_snapshots"] = [
        {
            "snapshot_id": row["snapshot_id"],
            "player_id": row["player_id"],
            "team_id": row["team_id"],
            "club_name": row.get("club_name"),
            "club_source_id": row.get("club_source_id"),
            "as_of": row["as_of"],
            "club_recent_matches": row.get("club_recent_matches"),
            "club_recent_starts": row.get("club_recent_starts"),
            "club_recent_minutes": row.get("club_recent_minutes"),
            "club_recent_goals": row.get("club_recent_goals"),
            "club_recent_assists": row.get("club_recent_assists"),
            "national_recent_caps": row.get("national_recent_caps"),
            "national_recent_starts": row.get("national_recent_starts"),
            "national_recent_minutes": row.get("national_recent_minutes"),
            "national_recent_goals": row.get("national_recent_goals"),
            "national_recent_assists": row.get("national_recent_assists"),
            "source": row.get("source") or SOURCE_NAME,
            "source_player_id": row["source_player_id"],
            "available_at": row["available_at"],
        }
        for row in records["player_form_snapshots"]
        if isinstance(row, dict)
    ]

    facts["team_strength_snapshots"] = [
        {
            "snapshot_id": row["snapshot_id"],
            "team_id": row["team_id"],
            "strength_type": row["strength_type"],
            "strength_value": row["strength_value"],
            "strength_source": row["strength_source"],
            "source": row.get("source") or row["strength_source"],
            "source_team_id": row["source_team_id"],
            "as_of": row["as_of"],
            "available_at": row["available_at"],
        }
        for row in records["team_strength_snapshots"]
        if isinstance(row, dict)
    ]
    return facts


def import_bootstrap_bundle(base_dir: Path, repository: ResearchDatabaseRepository) -> dict[str, Any]:
    payload = build_bootstrap_import_payload(base_dir)
    upserted = repository.upsert_facts(payload)
    return {
        "status": "ok",
        "database_path": str(repository.db_path),
        "upserted": upserted,
        "row_counts": repository.row_counts(),
    }


def write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _localized(value: Any) -> str:
    if isinstance(value, list) and value:
        item = value[0]
        if isinstance(item, dict):
            return str(item.get("Description") or "")
    if isinstance(value, str):
        return value
    return ""


def _extract_fifa_code(flag_url: str) -> str:
    if not flag_url:
        return ""
    tail = flag_url.rstrip("/").split("/")[-1]
    return tail.split("{", 1)[0].strip("-").upper()


def _player_display_name(player: dict[str, Any]) -> str:
    known = str(player.get("knownName") or "").strip()
    if known:
        return known
    first = str(player.get("firstName") or "").strip()
    last = str(player.get("lastName") or "").strip()
    return " ".join(part for part in (first, last) if part).strip()


def _team_aliases(name: str, fifa_code: str) -> list[str]:
    aliases = {name, fifa_code, _slug(name).replace("_", " ")}
    if fifa_code == "USA":
        aliases.add("United States")
        aliases.add("USMNT")
    if name == "Korea Republic":
        aliases.add("South Korea")
    if name == "IR Iran":
        aliases.add("Iran")
    return sorted(alias for alias in aliases if alias)


def _normalize_key(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
        .lower()
    )
    return " ".join(ascii_value.split())


def _slug(value: str) -> str:
    normalized = _normalize_key(value)
    slug = "".join(char if char.isalnum() else "_" for char in normalized)
    return "_".join(part for part in slug.split("_") if part)


def _bool_from_text(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--accessed-at", required=True)
    parser.add_argument("--top-players-per-team", type=int, default=DEFAULT_TOP_PLAYERS_PER_TEAM)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--import-db", action="store_true")
    args = parser.parse_args()

    result = build_bootstrap_bundle(
        output_dir=args.output_dir,
        accessed_at=args.accessed_at,
        top_players_per_team=args.top_players_per_team,
    )
    if args.import_db:
        repository = ResearchDatabaseRepository(args.db_path)
        result["db_import"] = import_bootstrap_bundle(args.output_dir, repository)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
