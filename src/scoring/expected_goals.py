from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MIN_EXPECTED_GOALS = 0.05
MAX_EXPECTED_GOALS = 5.0
DEFAULT_HOME_ADVANTAGE_FACTOR = 1.0
OPTIONAL_ADJUSTMENT_BOUNDS = (0.80, 1.20)
OPTIONAL_MAX_EFFECTS = {
    "lineup_integrity": 0.10,
    "key_player_status": 0.08,
    "schedule_fatigue": 0.06,
    "motivation_stage": 0.04,
}


class ExpectedGoalsError(ValueError):
    """Raised when expected-goals inference receives an invalid feature contract."""


@dataclass(frozen=True)
class ExpectedGoalsParams:
    home_advantage_factor: float = DEFAULT_HOME_ADVANTAGE_FACTOR
    optional_adjustments_enabled: bool = False
    min_expected_goals: float = MIN_EXPECTED_GOALS
    max_expected_goals: float = MAX_EXPECTED_GOALS
    parameter_set_id: str = "scoreline-poisson-v1-default"

    def validate(self) -> None:
        if not math.isfinite(self.home_advantage_factor) or self.home_advantage_factor <= 0:
            raise ExpectedGoalsError("home_advantage_factor must be positive and finite")
        if self.min_expected_goals <= 0:
            raise ExpectedGoalsError("min_expected_goals must be positive")
        if self.max_expected_goals <= self.min_expected_goals:
            raise ExpectedGoalsError("max_expected_goals must exceed min_expected_goals")


def infer_expected_goals(
    feature_vector: dict[str, Any],
    components: list[dict[str, Any]],
    *,
    params: ExpectedGoalsParams | None = None,
) -> dict[str, Any]:
    team_features = feature_vector.get("team_features")
    if not isinstance(team_features, dict):
        raise ExpectedGoalsError("feature vector missing team_features")
    match = feature_vector.get("match")
    if not isinstance(match, dict):
        raise ExpectedGoalsError("feature vector missing match")
    return infer_expected_goals_from_team_features(
        team_features.get("home"),
        team_features.get("away"),
        neutral_field=bool(match.get("neutral_field")),
        components=components,
        params=params,
    )


def infer_expected_goals_from_team_features(
    home: Any,
    away: Any,
    *,
    neutral_field: bool,
    components: list[dict[str, Any]] | None = None,
    params: ExpectedGoalsParams | None = None,
) -> dict[str, Any]:
    if not isinstance(home, dict) or not isinstance(away, dict):
        raise ExpectedGoalsError("home and away team features must be mappings")
    resolved = params or ExpectedGoalsParams()
    resolved.validate()
    prior_home = _positive_number(home.get("prior_goals_per_team"), fallback=1.25)
    prior_away = _positive_number(away.get("prior_goals_per_team"), fallback=prior_home)
    prior_mu = (prior_home + prior_away) / 2.0
    home_goals_for = _rate(home, "shrunk_goals_for", "goals_for_per_match", prior_mu)
    home_goals_against = _rate(home, "shrunk_goals_against", "goals_against_per_match", prior_mu)
    away_goals_for = _rate(away, "shrunk_goals_for", "goals_for_per_match", prior_mu)
    away_goals_against = _rate(away, "shrunk_goals_against", "goals_against_per_match", prior_mu)

    home_lambda_base = home_goals_for * away_goals_against / prior_mu
    away_lambda_base = away_goals_for * home_goals_against / prior_mu
    applied_home_advantage = 1.0 if neutral_field else resolved.home_advantage_factor
    home_optional_factor = 1.0
    away_optional_factor = 1.0
    adjustment_details: list[dict[str, Any]] = []
    if resolved.optional_adjustments_enabled:
        home_optional_factor, away_optional_factor, adjustment_details = _optional_adjustments(
            components or []
        )

    unclamped_home = home_lambda_base * applied_home_advantage * home_optional_factor
    unclamped_away = away_lambda_base * away_optional_factor
    home_expected_goals, home_clamp = _clamp_lambda(
        unclamped_home,
        minimum=resolved.min_expected_goals,
        maximum=resolved.max_expected_goals,
        side="home",
    )
    away_expected_goals, away_clamp = _clamp_lambda(
        unclamped_away,
        minimum=resolved.min_expected_goals,
        maximum=resolved.max_expected_goals,
        side="away",
    )
    clamps = [item for item in (home_clamp, away_clamp) if item is not None]
    return {
        "parameter_set_id": resolved.parameter_set_id,
        "prior_goals_per_team": round(prior_mu, 6),
        "home_goals_for_rate": round(home_goals_for, 6),
        "home_goals_against_rate": round(home_goals_against, 6),
        "away_goals_for_rate": round(away_goals_for, 6),
        "away_goals_against_rate": round(away_goals_against, 6),
        "home_lambda_base": round(home_lambda_base, 6),
        "away_lambda_base": round(away_lambda_base, 6),
        "neutral_field": neutral_field,
        "home_advantage_factor": round(applied_home_advantage, 6),
        "optional_adjustments_enabled": resolved.optional_adjustments_enabled,
        "home_optional_adjustment_factor": round(home_optional_factor, 6),
        "away_optional_adjustment_factor": round(away_optional_factor, 6),
        "adjustment_details": adjustment_details,
        "home_expected_goals": round(home_expected_goals, 6),
        "away_expected_goals": round(away_expected_goals, 6),
        "lambda_clamps": clamps,
    }


def _optional_adjustments(
    components: list[dict[str, Any]],
) -> tuple[float, float, list[dict[str, Any]]]:
    home_factor = 1.0
    away_factor = 1.0
    details: list[dict[str, Any]] = []
    indexed = {
        str(component.get("dimension")): component
        for component in components
        if isinstance(component, dict)
    }
    for dimension, max_effect in OPTIONAL_MAX_EFFECTS.items():
        component = indexed.get(dimension)
        if component is None:
            continue
        quality = _bounded_number(component.get("quality_score"), 0.0, 1.0, fallback=0.0)
        home_score = _bounded_number(component.get("home_value"), 0.0, 100.0, fallback=50.0)
        away_score = _bounded_number(component.get("away_value"), 0.0, 100.0, fallback=50.0)
        beta = math.log1p(max_effect)
        home_component_factor = math.exp(beta * ((home_score - 50.0) / 50.0) * quality)
        away_component_factor = math.exp(beta * ((away_score - 50.0) / 50.0) * quality)
        home_factor *= home_component_factor
        away_factor *= away_component_factor
        details.append(
            {
                "dimension": dimension,
                "quality_score": round(quality, 6),
                "home_factor": round(home_component_factor, 6),
                "away_factor": round(away_component_factor, 6),
            }
        )
    minimum, maximum = OPTIONAL_ADJUSTMENT_BOUNDS
    return (
        min(maximum, max(minimum, home_factor)),
        min(maximum, max(minimum, away_factor)),
        details,
    )


def _rate(
    features: dict[str, Any],
    primary: str,
    fallback_key: str,
    prior: float,
) -> float:
    value = features.get(primary)
    if value in (None, ""):
        value = features.get(fallback_key)
    return _positive_number(value, fallback=prior)


def _positive_number(value: Any, *, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number) or number <= 0:
        return fallback
    return number


def _bounded_number(
    value: Any,
    minimum: float,
    maximum: float,
    *,
    fallback: float,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number):
        return fallback
    return min(maximum, max(minimum, number))


def _clamp_lambda(
    value: float,
    *,
    minimum: float,
    maximum: float,
    side: str,
) -> tuple[float, dict[str, Any] | None]:
    clamped = min(maximum, max(minimum, value))
    if math.isclose(clamped, value, rel_tol=0.0, abs_tol=1e-12):
        return clamped, None
    return clamped, {
        "side": side,
        "unclamped": round(value, 6),
        "clamped": round(clamped, 6),
        "minimum": minimum,
        "maximum": maximum,
    }
