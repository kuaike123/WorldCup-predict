from __future__ import annotations

from typing import Any, Callable

from .field_normalizer import (
    average_available,
    bool_score,
    clamp,
    goal_diff_to_score,
    inverse_rate_score,
    positive_rate_score,
    ppg_to_score,
    ratio_to_score,
)


DimensionCalc = Callable[[dict[str, Any]], tuple[list[float | None], dict[str, Any]]]


def _team_strength(team: dict[str, Any]) -> tuple[list[float | None], dict[str, Any]]:
    fields = ["elo_score", "fifa_rank_score", "squad_value_score"]
    return [team.get(field) for field in fields], {field: team.get(field) for field in fields}


def _recent_form(team: dict[str, Any]) -> tuple[list[float | None], dict[str, Any]]:
    values = [
        ppg_to_score(team.get("recent_points_per_game")),
        goal_diff_to_score(team.get("last_5_goal_diff")),
        ratio_to_score(team.get("unbeaten_rate")),
    ]
    raw = {
        "recent_points_per_game": team.get("recent_points_per_game"),
        "last_5_goal_diff": team.get("last_5_goal_diff"),
        "unbeaten_rate": team.get("unbeaten_rate"),
    }
    return values, raw


def _lineup_integrity(team: dict[str, Any]) -> tuple[list[float | None], dict[str, Any]]:
    values = [
        ratio_to_score(team.get("expected_starters_available_ratio")),
        team.get("injury_impact_score"),
    ]
    raw = {
        "expected_starters_available_ratio": team.get("expected_starters_available_ratio"),
        "injury_impact_score": team.get("injury_impact_score"),
    }
    return values, raw


def _key_player_status(team: dict[str, Any]) -> tuple[list[float | None], dict[str, Any]]:
    values = [
        ratio_to_score(team.get("key_players_available_ratio")),
        team.get("key_player_form_score"),
    ]
    raw = {
        "key_players_available_ratio": team.get("key_players_available_ratio"),
        "key_player_form_score": team.get("key_player_form_score"),
    }
    return values, raw


def _attack_defense_efficiency(team: dict[str, Any]) -> tuple[list[float | None], dict[str, Any]]:
    values = [
        positive_rate_score(team.get("xg_for"), 28, 25),
        inverse_rate_score(team.get("xg_against"), 28, 90),
        positive_rate_score(team.get("goals_for_per_match"), 25, 25),
        inverse_rate_score(team.get("goals_against_per_match"), 25, 90),
    ]
    raw = {
        "xg_for": team.get("xg_for"),
        "xg_against": team.get("xg_against"),
        "goals_for_per_match": team.get("goals_for_per_match"),
        "goals_against_per_match": team.get("goals_against_per_match"),
    }
    return values, raw


def _schedule_fatigue(team: dict[str, Any]) -> tuple[list[float | None], dict[str, Any]]:
    rest_days = team.get("rest_days")
    travel_km = team.get("travel_km")
    values = [
        clamp(45 + min(float(rest_days), 7) * 7) if rest_days is not None else None,
        clamp(100 - float(travel_km) / 80) if travel_km is not None else None,
        bool_score(team.get("extra_time_recent"), 40, 75),
        clamp(70 + float(team["climate_adjustment"])) if "climate_adjustment" in team else None,
    ]
    raw = {
        "rest_days": rest_days,
        "travel_km": travel_km,
        "extra_time_recent": team.get("extra_time_recent"),
        "climate_adjustment": team.get("climate_adjustment"),
    }
    return values, raw


def _motivation_stage(team: dict[str, Any]) -> tuple[list[float | None], dict[str, Any]]:
    values = [
        team.get("motivation_score"),
        bool_score(team.get("must_win"), 82, 62),
        bool_score(team.get("knockout"), 72, 62),
    ]
    raw = {
        "motivation_score": team.get("motivation_score"),
        "must_win": team.get("must_win"),
        "knockout": team.get("knockout"),
    }
    return values, raw


DIMENSION_CALCS: dict[str, DimensionCalc] = {
    "team_strength": _team_strength,
    "recent_form": _recent_form,
    "lineup_integrity": _lineup_integrity,
    "key_player_status": _key_player_status,
    "attack_defense_efficiency": _attack_defense_efficiency,
    "schedule_fatigue": _schedule_fatigue,
    "motivation_stage": _motivation_stage,
}


def calculate_dimension_scores(match: dict[str, Any], market_signal: dict[str, Any]) -> list[dict[str, Any]]:
    home = match["team_features"]["home"]
    away = match["team_features"]["away"]
    breakdown: list[dict[str, Any]] = []

    for dimension, calculator in DIMENSION_CALCS.items():
        home_values, home_raw = calculator(home)
        away_values, away_raw = calculator(away)
        home_score, home_quality = average_available(home_values)
        away_score, away_quality = average_available(away_values)
        expected_fields = sorted(set(home_raw) | set(away_raw))
        missing = [
            f"home.{field}" for field, value in home_raw.items() if value is None
        ] + [
            f"away.{field}" for field, value in away_raw.items() if value is None
        ]
        quality = round((home_quality + away_quality) / 2, 3)
        breakdown.append({
            "dimension": dimension,
            "home_raw_value": home_raw,
            "away_raw_value": away_raw,
            "home_score": home_score,
            "away_score": away_score,
            "data_sources": [f"team_features.home.{field}" for field in expected_fields]
            + [f"team_features.away.{field}" for field in expected_fields],
            "missing_fields": missing,
            "quality_score": quality,
            "explanation": f"{dimension} 基于 {len(expected_fields)} 类字段计算，质量分 {quality:.2f}。"
        })

    market_score = float(market_signal["score"])
    home_market = market_score if market_signal["direction"].startswith("home") else 100 - market_score
    away_market = market_score if market_signal["direction"].startswith("away") else 100 - market_score
    if "neutral" in market_signal["direction"]:
        home_market = away_market = 50
    breakdown.append({
        "dimension": "odds_movement",
        "home_raw_value": market_signal,
        "away_raw_value": market_signal,
        "home_score": round(clamp(home_market), 2),
        "away_score": round(clamp(away_market), 2),
        "data_sources": ["odds_snapshots"],
        "missing_fields": [] if market_signal["quality_score"] > 0 else ["odds_snapshots"],
        "quality_score": market_signal["quality_score"],
        "explanation": market_signal["explanation"]
    })
    return breakdown
