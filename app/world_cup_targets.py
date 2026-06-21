from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


WORLD_CUP_2026_COMPETITION_NAME = "2026 FIFA World Cup"
WORLD_CUP_2026_YEAR = "2026"
WORLD_CUP_2026_FIXTURE_PREFIX = "fixture_wc2026_"
_EXCLUDED_COMPETITION_TOKENS = (
    "qualif",
    "qualification",
    "friendly",
    "int friendly",
    "international friendly",
    "wcq",
)


def canonical_world_cup_2026_competition(
    competition: str | None,
    *,
    season: str | None = None,
    match_time: str | None = None,
    fixture_id: str | None = None,
    source_fixture_id: str | None = None,
) -> str:
    payload = {
        "competition": competition,
        "season": season,
        "match_time": match_time,
        "fixture_id": fixture_id,
        "source_fixture_id": source_fixture_id,
    }
    if is_world_cup_2026_fixture(payload):
        return WORLD_CUP_2026_COMPETITION_NAME
    return str(competition or "").strip() or "Unknown"


def is_world_cup_2026_fixture(fixture: dict[str, Any]) -> bool:
    competition = _normalized_text(
        fixture.get("competition")
        or fixture.get("competition_name")
        or fixture.get("league")
        or fixture.get("tournament")
    )
    season = _normalized_text(fixture.get("season") or fixture.get("season_name") or fixture.get("stage"))
    fixture_id = str(fixture.get("fixture_id") or fixture.get("match_id") or "").casefold()
    source_fixture_id = str(
        fixture.get("source_fixture_id")
        or fixture.get("external_match_id")
        or fixture.get("sport_event_id")
        or ""
    ).casefold()
    match_year = _match_year(str(fixture.get("match_time") or fixture.get("kickoff_utc") or fixture.get("start_time") or ""))

    searchable = " ".join(item for item in (competition, season, fixture_id, source_fixture_id) if item)
    if any(token in searchable for token in _EXCLUDED_COMPETITION_TOKENS):
        return False

    has_world_cup_identity = (
        "world cup" in competition
        or "world cup" in season
        or fixture_id.startswith(WORLD_CUP_2026_FIXTURE_PREFIX)
        or "wc2026" in source_fixture_id
    )
    has_2026_identity = (
        WORLD_CUP_2026_YEAR == match_year
        or WORLD_CUP_2026_YEAR in competition
        or WORLD_CUP_2026_YEAR in season
        or fixture_id.startswith(WORLD_CUP_2026_FIXTURE_PREFIX)
        or "wc2026" in source_fixture_id
    )
    return has_world_cup_identity and has_2026_identity


def world_cup_2026_target_key(fixture: dict[str, Any]) -> str:
    source_fixture_id = str(
        fixture.get("source_fixture_id")
        or fixture.get("external_match_id")
        or fixture.get("sport_event_id")
        or ""
    ).strip()
    sr_event = _sportradar_event_key(source_fixture_id)
    if sr_event:
        return sr_event
    fixture_id = str(fixture.get("fixture_id") or fixture.get("match_id") or "").strip()
    if fixture_id.startswith("sportradar_"):
        suffix = fixture_id.removeprefix("sportradar_")
        if suffix:
            return f"sr:sport_event:{suffix}"
    if source_fixture_id:
        source = str(fixture.get("source") or "").strip()
        return f"{source}:{source_fixture_id}" if source else source_fixture_id
    return fixture_id


def normalize_world_cup_2026_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(fixture)
    if is_world_cup_2026_fixture(normalized):
        normalized["competition"] = WORLD_CUP_2026_COMPETITION_NAME
    return normalized


def _normalized_text(value: Any) -> str:
    text = str(value or "").replace("\u2122", " ").replace("\u00ae", " ")
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _match_year(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        return text[:4]
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y")


def _sportradar_event_key(value: str) -> str:
    text = str(value or "").strip()
    match = re.search(r"sr:sport_event:(\d+)", text)
    if match:
        return f"sr:sport_event:{match.group(1)}"
    return ""
