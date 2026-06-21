from __future__ import annotations

from datetime import datetime
from typing import Any


HOST_TEAM_MARKERS = {
    "mexico",
    "méxico",
    "mex",
    "united states",
    "usa",
    "us",
    "canada",
    "can",
}


def build_motivation_context(
    fixture: dict[str, Any],
    home_team: dict[str, Any],
    away_team: dict[str, Any],
) -> dict[str, Any]:
    fixture_id = str(fixture.get("fixture_id") or "")
    stage = _stage(fixture)
    rules: list[str] = []
    reason_codes: list[str] = []
    home_score = 60.0
    away_score = 60.0
    quality = 0.45

    if _is_opening_match(fixture):
        rules.append("opening_match_high_motivation")
        reason_codes.append("first_match_baseline_high")
        home_score += 8
        away_score += 6
        quality = max(quality, 0.65)

    host_side = _host_team_side(home_team, away_team)
    if host_side == "home":
        rules.append("host_home_motivation_boost")
        reason_codes.append("host_home_boost")
        home_score += 6
        quality = max(quality, 0.72)
    elif host_side == "away":
        rules.append("host_away_motivation_boost")
        reason_codes.append("host_away_boost")
        away_score += 6
        quality = max(quality, 0.72)

    if "matchday_3" in stage or "group_stage_final_round" in stage:
        rules.append("group_stage_final_round_context")
        reason_codes.append("qualification_context_required")
        quality = max(quality, 0.58)
    elif "group_stage" in stage or "matchday_1" in stage:
        rules.append("group_stage_baseline")
        reason_codes.append("limited_standings_context")

    if _is_knockout(stage):
        rules.append("knockout_must_win_context")
        reason_codes.append("knockout_no_draw_settlement")
        home_score += 5
        away_score += 5
        quality = max(quality, 0.70)

    if _has_context_flag(fixture, "must_win_home"):
        rules.append("home_must_win_context")
        reason_codes.append("home_must_take_points")
        home_score += 8
        quality = max(quality, 0.75)
    if _has_context_flag(fixture, "must_win_away"):
        rules.append("away_must_win_context")
        reason_codes.append("away_must_take_points")
        away_score += 8
        quality = max(quality, 0.75)
    if _has_context_flag(fixture, "home_already_qualified"):
        rules.append("home_already_qualified_context")
        reason_codes.append("home_motivation_uncertain_after_qualification")
        home_score -= 5
    if _has_context_flag(fixture, "away_already_qualified"):
        rules.append("away_already_qualified_context")
        reason_codes.append("away_motivation_uncertain_after_qualification")
        away_score -= 5

    status = "ok" if rules and quality >= 0.65 else "partial"
    if not rules:
        reason_codes.append("motivation_context_insufficient")

    return {
        "fixture_id": fixture_id,
        "stage": stage,
        "host_team_side": host_side,
        "rules_applied": rules,
        "analysis_required": True,
        "motivation_score_home": round(max(0.0, min(home_score, 100.0)), 2),
        "motivation_score_away": round(max(0.0, min(away_score, 100.0)), 2),
        "quality_score": round(quality, 3),
        "status": status,
        "reason_codes": sorted(set(reason_codes)),
        "llm_may_explain": True,
        "llm_must_not_invent_facts": True,
    }


def _stage(fixture: dict[str, Any]) -> str:
    values = [
        fixture.get("stage"),
        fixture.get("round"),
        fixture.get("matchday"),
        fixture.get("season"),
        fixture.get("competition"),
    ]
    text = " ".join(str(value or "") for value in values).casefold()
    fixture_id = str(fixture.get("fixture_id") or "").casefold()
    if "opening" in fixture_id or _match_date(fixture) == "2026-06-11":
        return "group_stage_matchday_1"
    if "knockout" in text or "round of" in text or "quarter" in text or "semi" in text or "final" in text:
        return "knockout_stage"
    if "matchday 3" in text or "matchday_3" in text or "last round" in text:
        return "group_stage_matchday_3"
    if "group" in text or "world cup" in text:
        return "group_stage"
    return "unknown"


def _is_opening_match(fixture: dict[str, Any]) -> bool:
    fixture_id = str(fixture.get("fixture_id") or "").casefold()
    return "opening" in fixture_id or _match_date(fixture) == "2026-06-11"


def _host_team_side(home_team: dict[str, Any], away_team: dict[str, Any]) -> str | None:
    home_markers = _team_markers(home_team)
    away_markers = _team_markers(away_team)
    if home_markers & HOST_TEAM_MARKERS:
        return "home"
    if away_markers & HOST_TEAM_MARKERS:
        return "away"
    return None


def _team_markers(team: dict[str, Any]) -> set[str]:
    return {
        str(value).casefold()
        for value in (
            team.get("canonical_name"),
            team.get("country_code"),
            team.get("fifa_code"),
            team.get("source_team_id"),
        )
        if str(value or "").strip()
    }


def _is_knockout(stage: str) -> bool:
    return "knockout" in stage or "quarter" in stage or "semi" in stage or "final" in stage


def _has_context_flag(fixture: dict[str, Any], flag: str) -> bool:
    flags = fixture.get("motivation_flags")
    if isinstance(flags, list):
        return flag in {str(item) for item in flags}
    return bool(fixture.get(flag))


def _match_date(fixture: dict[str, Any]) -> str | None:
    value = str(fixture.get("match_time") or "")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None
