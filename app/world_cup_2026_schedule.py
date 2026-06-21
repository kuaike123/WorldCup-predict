from __future__ import annotations

import csv
import json
import re
from datetime import UTC, datetime
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.world_cup_targets import (
    WORLD_CUP_2026_COMPETITION_NAME,
    WORLD_CUP_2026_FIXTURE_PREFIX,
    canonical_world_cup_2026_competition,
    is_world_cup_2026_fixture,
)


DEFAULT_FROZEN_SCHEDULE_DIR = ROOT / "data" / "research_import" / "p0_11"
DEFAULT_FROZEN_SCHEDULE_FIXTURES_PATH = DEFAULT_FROZEN_SCHEDULE_DIR / "world_cup_2026_fixtures.csv"
DEFAULT_FROZEN_SCHEDULE_MANIFEST_PATH = DEFAULT_FROZEN_SCHEDULE_DIR / "world_cup_2026_schedule_manifest.json"


def load_frozen_schedule_manifest(path: Path = DEFAULT_FROZEN_SCHEDULE_MANIFEST_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_frozen_schedule_rows(path: Path = DEFAULT_FROZEN_SCHEDULE_FIXTURES_PATH) -> list[dict[str, str]]:
    return _load_frozen_schedule_rows_cached(str(path))


def frozen_schedule_fixture_map(path: Path = DEFAULT_FROZEN_SCHEDULE_FIXTURES_PATH) -> dict[str, dict[str, str]]:
    return {
        row["fixture_id"]: row
        for row in load_frozen_schedule_rows(path)
        if str(row.get("fixture_id") or "").strip()
    }


def frozen_schedule_row_for_fixture(
    fixture: dict[str, Any],
    *,
    path: Path = DEFAULT_FROZEN_SCHEDULE_FIXTURES_PATH,
) -> dict[str, str] | None:
    if not is_world_cup_2026_fixture(fixture):
        return None

    fixture_id = str(fixture.get("fixture_id") or fixture.get("match_id") or "").strip()
    source_fixture_id = str(
        fixture.get("source_fixture_id")
        or fixture.get("external_match_id")
        or fixture.get("sport_event_id")
        or ""
    ).strip()
    incoming_aliases = _fixture_reference_aliases(fixture_id) | _fixture_reference_aliases(source_fixture_id)
    for row in load_frozen_schedule_rows(path):
        if (
            incoming_aliases & _fixture_reference_aliases(str(row.get("fixture_id") or ""))
            or incoming_aliases & _fixture_reference_aliases(str(row.get("source_fixture_id") or ""))
        ):
            return row
    for row in load_frozen_schedule_rows(path):
        if _canonical_fixture_identity_matches_row(fixture, row):
            return row
    return None


def is_frozen_schedule_fixture_id(
    fixture_id: str,
    *,
    path: Path = DEFAULT_FROZEN_SCHEDULE_FIXTURES_PATH,
) -> bool:
    return str(fixture_id or "").strip() in frozen_schedule_fixture_map(path)


def is_schedule_backed_world_cup_2026_fixture(
    fixture: dict[str, Any],
    *,
    strict_official_ids: bool = True,
    allow_explicit_test_fixtures: bool = False,
    path: Path = DEFAULT_FROZEN_SCHEDULE_FIXTURES_PATH,
) -> bool:
    if not is_world_cup_2026_fixture(fixture):
        return False

    fixture_id = str(fixture.get("fixture_id") or fixture.get("match_id") or "").strip()
    if frozen_schedule_row_for_fixture(fixture, path=path) is not None:
        return True
    if strict_official_ids and fixture_id.startswith(WORLD_CUP_2026_FIXTURE_PREFIX):
        return False
    return allow_explicit_test_fixtures


def frozen_schedule_contract_report(
    *,
    fixtures_path: Path = DEFAULT_FROZEN_SCHEDULE_FIXTURES_PATH,
    manifest_path: Path = DEFAULT_FROZEN_SCHEDULE_MANIFEST_PATH,
) -> dict[str, Any]:
    rows = load_frozen_schedule_rows(fixtures_path)
    manifest = load_frozen_schedule_manifest(manifest_path)
    errors: list[dict[str, Any]] = []
    invalid_rows = [row for row in rows if not is_world_cup_2026_fixture(row)]
    if invalid_rows:
        errors.append({
            "reason": "non_target_fixture_rows_present",
            "fixture_ids": [str(row.get("fixture_id") or "") for row in invalid_rows[:20]],
            "count": len(invalid_rows),
        })
    non_canonical_competitions = sorted({
        str(row.get("competition") or "")
        for row in rows
        if str(row.get("competition") or "") != WORLD_CUP_2026_COMPETITION_NAME
    })
    if non_canonical_competitions:
        errors.append({
            "reason": "non_canonical_competition_labels_present",
            "values": non_canonical_competitions,
        })
    non_2026_rows = [
        row
        for row in rows
        if not str(row.get("match_time") or "").startswith("2026-")
    ]
    if non_2026_rows:
        errors.append({
            "reason": "non_2026_match_rows_present",
            "fixture_ids": [str(row.get("fixture_id") or "") for row in non_2026_rows[:20]],
            "count": len(non_2026_rows),
        })
    hard_coded_knockout_teams = [
        row
        for row in rows
        if str(row.get("stage") or "") not in {"", "First Stage"}
        and str(row.get("home_team_id") or "").strip()
        and str(row.get("away_team_id") or "").strip()
    ]
    if hard_coded_knockout_teams:
        errors.append({
            "reason": "hard_coded_knockout_teams_present",
            "fixture_ids": [str(row.get("fixture_id") or "") for row in hard_coded_knockout_teams[:20]],
            "count": len(hard_coded_knockout_teams),
        })

    return {
        "valid": not errors,
        "fixtures_path": str(fixtures_path),
        "manifest_path": str(manifest_path),
        "summary": _frozen_schedule_summary(rows),
        "manifest": manifest,
        "errors": errors,
    }


@lru_cache(maxsize=8)
def _load_frozen_schedule_rows_cached(path_text: str) -> list[dict[str, str]]:
    path = Path(path_text)
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    normalized_rows: list[dict[str, str]] = []
    for row in rows:
        fixture_id = str(row.get("fixture_id") or "").strip()
        source_fixture_id = str(row.get("source_fixture_id") or "").strip()
        match_time = str(row.get("match_time") or "").strip()
        normalized_rows.append({
            **{key: str(value or "") for key, value in row.items()},
            "fixture_id": fixture_id,
            "source_fixture_id": source_fixture_id,
            "match_time": match_time,
            "season": "2026",
            "competition": canonical_world_cup_2026_competition(
                row.get("competition"),
                season=row.get("season"),
                match_time=match_time,
                fixture_id=fixture_id,
                source_fixture_id=source_fixture_id,
            ),
        })
    return normalized_rows


def _frozen_schedule_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    stages = Counter(str(row.get("stage") or "") for row in rows)
    groups = Counter(str(row.get("group_name") or "") for row in rows if str(row.get("group_name") or "").strip())
    competitions = sorted({str(row.get("competition") or "") for row in rows if str(row.get("competition") or "").strip()})
    return {
        "fixtures_count": len(rows),
        "competition_names": competitions,
        "stages": dict(sorted((key, value) for key, value in stages.items() if key)),
        "groups": dict(sorted(groups.items())),
        "fixture_id_prefixes": sorted({
            fixture_id.split("_", 3)[0] + "_" + fixture_id.split("_", 3)[1] if fixture_id.count("_") >= 1 else fixture_id
            for fixture_id in (
                str(row.get("fixture_id") or "")
                for row in rows
            )
            if fixture_id
        }),
        "match_years": sorted({
            str(row.get("match_time") or "")[:4]
            for row in rows
            if str(row.get("match_time") or "")[:4].isdigit()
        }),
        "source_fixture_ids_count": sum(1 for row in rows if str(row.get("source_fixture_id") or "").strip()),
    }


def _fixture_reference_aliases(value: str) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    aliases = {text}
    digits = _trailing_digits(text)
    if digits:
        aliases.add(digits)
        aliases.add(f"sr:sport_event:{digits}")
        aliases.add(f"{WORLD_CUP_2026_FIXTURE_PREFIX}{digits}")
        aliases.add(f"sportradar_{digits}")
    return aliases


def _trailing_digits(value: str) -> str:
    match = re.search(r"(\d+)$", str(value or "").strip())
    if match:
        return match.group(1)
    return ""


def _canonical_fixture_identity_matches_frozen_schedule(
    fixture: dict[str, Any],
    *,
    path: Path,
) -> bool:
    for row in load_frozen_schedule_rows(path):
        if _canonical_fixture_identity_matches_row(fixture, row):
            return True
    return False


def _canonical_fixture_identity_matches_row(
    fixture: dict[str, Any],
    row: dict[str, str],
) -> bool:
    incoming_time = _canonical_match_time(
        fixture.get("match_time") or fixture.get("start_time") or fixture.get("kickoff_utc")
    )
    incoming_home = _normalized_team_token(fixture.get("home_team_id") or fixture.get("home_team"))
    incoming_away = _normalized_team_token(fixture.get("away_team_id") or fixture.get("away_team"))
    if not incoming_time or not incoming_home or not incoming_away:
        return False
    row_time = _canonical_match_time(row.get("match_time"))
    row_home = _normalized_team_token(row.get("home_team_id"))
    row_away = _normalized_team_token(row.get("away_team_id"))
    return incoming_time == row_time and incoming_home == row_home and incoming_away == row_away


def _canonical_match_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat()


def _normalized_team_token(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if text.startswith("team_"):
        text = text.removeprefix("team_")
    text = text.replace("&", " and ")
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return " ".join(text.split())
