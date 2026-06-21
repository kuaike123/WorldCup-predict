from __future__ import annotations

from datetime import datetime
from typing import Any

from .repository import ResearchDatabaseRepository

RECENT_RESULT_LIMIT = 10
FRIENDLY_COMPETITION_WEIGHT = 0.4
WORLD_CUP_QUALIFIER_WEIGHT = 0.85
OFFICIAL_COMPETITION_WEIGHT = 0.75
WORLD_CUP_WEIGHT = 1.0


REQUIRED_TEAM_FEATURES = (
    "recent_points_per_game",
    "last_5_goal_diff",
    "unbeaten_rate",
    "friendly_match_ratio",
    "goals_for_per_match",
    "goals_against_per_match",
    "rest_days",
)


class HistoricalFeatureExtractor:
    def __init__(self, repository: ResearchDatabaseRepository) -> None:
        self.repository = repository

    def extract_team_features(
        self,
        team_id: str,
        *,
        match_time: str,
        available_at_cutoff: str,
        limit: int = RECENT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        results = self.repository.recent_results_for_team(
            team_id,
            match_time=match_time,
            available_at_cutoff=available_at_cutoff,
            limit=limit,
        )
        blocked = self.repository.blocked_results_after_cutoff(
            team_id,
            match_time=match_time,
            available_at_cutoff=available_at_cutoff,
        )
        if not results:
            features = {field: None for field in REQUIRED_TEAM_FEATURES}
            return {
                "features": features,
                "covered_fields": [],
                "missing_fields": list(REQUIRED_TEAM_FEATURES),
                "blocked_by_available_at": [
                    _blocked_record(team_id, item) for item in blocked
                ],
                "source_audit": [],
                "sample_size": 0,
            }

        scored = [_score_result(team_id, result) for result in results]
        weights = [_result_weight(result, index) for index, result in enumerate(results)]
        weighted_totals = sum(weights) or 1.0
        latest_played_at = str(results[0].get("played_at") or results[0].get("match_time") or "")
        weighted_goal_diff = _weighted_average(
            [item["goals_for"] - item["goals_against"] for item in scored],
            weights,
        )
        weighted_friendly_share = sum(
            weight
            for result, weight in zip(results, weights, strict=False)
            if _is_friendly_competition(result.get("competition"))
        ) / weighted_totals

        features = {
            "recent_points_per_game": round(_weighted_average([item["points"] for item in scored], weights), 3),
            "last_5_goal_diff": round(weighted_goal_diff * min(len(scored), 5), 3),
            "unbeaten_rate": round(
                _weighted_average([1.0 if item["points"] > 0 else 0.0 for item in scored], weights),
                3,
            ),
            "goals_for_per_match": round(_weighted_average([item["goals_for"] for item in scored], weights), 3),
            "goals_against_per_match": round(
                _weighted_average([item["goals_against"] for item in scored], weights),
                3,
            ),
            "rest_days": _days_between(latest_played_at, match_time),
            "friendly_match_ratio": round(weighted_friendly_share, 3),
        }
        missing_fields = [
            field for field in REQUIRED_TEAM_FEATURES
            if features.get(field) is None
        ]
        return {
            "features": features,
            "covered_fields": [
                field for field in REQUIRED_TEAM_FEATURES
                if field not in missing_fields
            ],
            "missing_fields": missing_fields,
            "blocked_by_available_at": [
                _blocked_record(team_id, item) for item in blocked
            ],
            "source_audit": [_source_audit_record(item) for item in results],
            "sample_size": len(results),
        }


def _score_result(team_id: str, result: dict[str, Any]) -> dict[str, int]:
    is_home = result["home_team_id"] == team_id
    goals_for = int(result["home_score"] if is_home else result["away_score"])
    goals_against = int(result["away_score"] if is_home else result["home_score"])
    if goals_for > goals_against:
        points = 3
    elif goals_for == goals_against:
        points = 1
    else:
        points = 0
    return {
        "goals_for": goals_for,
        "goals_against": goals_against,
        "points": points,
    }


def _source_audit_record(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": result.get("source"),
        "source_record_type": "match_result",
        "source_record_id": result.get("source_result_id"),
        "record_id": result.get("result_id"),
        "available_at": result.get("available_at"),
    }


def _blocked_record(team_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "team_id": team_id,
        "result_id": result.get("result_id"),
        "fixture_id": result.get("fixture_id"),
        "source": result.get("source"),
        "source_record_id": result.get("source_result_id"),
        "available_at": result.get("available_at"),
        "reason": "available_after_cutoff",
    }


def _days_between(start: str, end: str) -> int | None:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if start_dt is None or end_dt is None:
        return None
    return max((end_dt - start_dt).days, 0)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _is_friendly_competition(value: Any) -> bool:
    normalized = "".join(char.lower() for char in str(value or "") if char.isalnum())
    return normalized in {"if", "friendly", "intfriendly", "internationalfriendly"}


def _competition_weight(value: Any) -> float:
    normalized = "".join(char.lower() for char in str(value or "") if char.isalnum())
    if not normalized:
        return OFFICIAL_COMPETITION_WEIGHT
    if normalized == "fifaworldcup":
        return WORLD_CUP_WEIGHT
    if "worldcup" in normalized and any(token in normalized for token in ("qual", "qualification", "qualifier")):
        return WORLD_CUP_QUALIFIER_WEIGHT
    if _is_friendly_competition(value):
        return FRIENDLY_COMPETITION_WEIGHT
    return OFFICIAL_COMPETITION_WEIGHT


def _recency_weight(index: int) -> float:
    if index < 3:
        return 1.0
    if index < 6:
        return 0.8
    return 0.6


def _result_weight(result: dict[str, Any], index: int) -> float:
    return _competition_weight(result.get("competition")) * _recency_weight(index)


def _weighted_average(values: list[float], weights: list[float]) -> float:
    total_weight = sum(weights)
    if total_weight <= 0:
        return 0.0
    return sum(value * weight for value, weight in zip(values, weights, strict=False)) / total_weight
