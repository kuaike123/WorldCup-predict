from __future__ import annotations

import math
from typing import Any

from .expected_goals import ExpectedGoalsError, infer_expected_goals
from .field_normalizer import (
    clamp,
    goal_diff_to_score,
    inverse_rate_score,
    positive_rate_score,
    ppg_to_score,
    ratio_to_score,
)
from .scoreline_model import ScorelineModelError, build_scoreline_distribution


P0_15_VERSION = "p0.15"
P0_15_WEIGHTS_VERSION = "p0.15-research-preview"
PROBABILITY_MODEL_LEGACY = "legacy_logistic"
PROBABILITY_MODEL_SCORELINE = "scoreline_poisson"
PROBABILITY_MODEL_ROUTED = "hybrid_routed"
DEFAULT_PROBABILITY_MODEL_MODE = PROBABILITY_MODEL_ROUTED
P0_15_COMPONENT_STATUS_VALUES = {
    "ok",
    "partial",
    "neutral_default",
    "unavailable",
    "blocked",
}
P0_15_COMPONENT_DIMENSIONS = (
    "team_strength",
    "recent_form",
    "attack_defense_efficiency",
    "schedule_fatigue",
    "key_player_status",
    "odds_movement",
    "lineup_integrity",
    "motivation_stage",
)
P0_15_RESEARCH_WEIGHTS = {
    "team_strength": 0.18,
    "recent_form": 0.16,
    "attack_defense_efficiency": 0.14,
    "schedule_fatigue": 0.08,
    "key_player_status": 0.12,
    "odds_movement": 0.10,
    "lineup_integrity": 0.15,
    "motivation_stage": 0.07,
}
P0_15_PROBABILITY_PARAMS = {
    "gap_divisor": 11.0,
    "draw_base": 0.34,
    "draw_slope": 0.006,
    "draw_min": 0.18,
    "draw_max": 0.36,
    "over_base": 0.36,
    "over_attack_scale": 120.0,
    "over_odds_scale": 0.08,
    "over_min": 0.25,
    "over_max": 0.72,
    "upset_base": 0.12,
    "upset_gap_threshold": 12.0,
    "upset_gap_scale": 45.0,
    "upset_min": 0.08,
    "upset_max": 0.42,
}


class PreMatchResearchScoringError(ValueError):
    """Raised when a P0.15 research-preview scoring contract is invalid."""


def analyze_research_feature_vector(feature_vector: dict[str, Any]) -> dict[str, Any]:
    return analyze_research_feature_vector_with_params(
        feature_vector,
        validate_contract=True,
    )


def analyze_research_feature_vector_with_params(
    feature_vector: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
    probability_params: dict[str, float] | None = None,
    probability_model_mode: str = DEFAULT_PROBABILITY_MODEL_MODE,
    scoreline_params: dict[str, Any] | None = None,
    weights_version: str = P0_15_WEIGHTS_VERSION,
    validate_contract: bool = False,
) -> dict[str, Any]:
    if probability_model_mode not in {PROBABILITY_MODEL_LEGACY, PROBABILITY_MODEL_ROUTED}:
        raise PreMatchResearchScoringError(
            f"unsupported probability_model_mode:{probability_model_mode}"
        )
    _validate_feature_vector(feature_vector)
    home = feature_vector["team_features"]["home"]
    away = feature_vector["team_features"]["away"]
    components = [
        _team_strength_component(home, away),
        _recent_form_component(home, away),
        _attack_defense_component(home, away),
        _schedule_fatigue_component(home, away),
        _key_player_status_component(home, away),
        _odds_movement_component(feature_vector),
        _lineup_integrity_component(feature_vector),
        _motivation_stage_component(feature_vector),
    ]
    home_score, away_score = _weighted_team_scores(components, weights=weights)
    score_gap = round(home_score - away_score, 2)
    legacy_probabilities = _probabilities(
        home_score,
        away_score,
        components,
        feature_vector,
        probability_params=probability_params,
    )
    probabilities, prediction_routing, scoreline_payload = _routed_probabilities(
        feature_vector=feature_vector,
        components=components,
        legacy_probabilities=legacy_probabilities,
        probability_model_mode=probability_model_mode,
        scoreline_params=scoreline_params,
    )
    risk = _risk(score_gap, components, probabilities)
    prediction = {
        "version": P0_15_VERSION,
        "fixture_id": feature_vector["fixture_id"],
        "match_id": feature_vector["match_id"],
        "home_team": feature_vector["home_team"]["name"],
        "away_team": feature_vector["away_team"]["name"],
        "generated_at": feature_vector["generated_at"],
        "as_of": feature_vector["as_of"],
        "weights_version": weights_version,
        "not_used_in_production_scoring_by_default": True,
        "team_scores": {
            "home": home_score,
            "away": away_score,
            "score_gap": score_gap,
        },
        "probabilities": probabilities,
        "probability_model_mode": probability_model_mode,
        "prediction_routing": prediction_routing,
        "risk": risk,
        "components": components,
        "coverage": _coverage(components),
        "input_summary": {
            "home_team_id": feature_vector["home_team"]["team_id"],
            "away_team_id": feature_vector["away_team"]["team_id"],
            "match_time": feature_vector["match"]["match_time"],
            "competition": feature_vector["match"].get("competition"),
            "neutral_field": feature_vector["match"].get("neutral_field"),
            "odds_status": feature_vector["odds"]["status"],
            "pre_match_crawler_snapshot_id": feature_vector.get("pre_match_crawler_snapshot", {}).get("snapshot_id"),
            "pre_match_crawler_status": feature_vector.get("coverage", {}).get("pre_match_crawler_status", "unavailable"),
            "pre_match_news_snapshot_id": feature_vector.get("pre_match_news_snapshot", {}).get("snapshot_id"),
            "pre_match_news_status": feature_vector.get("coverage", {}).get("pre_match_news_status", "unavailable"),
            "motivation_context": feature_vector.get("motivation_context", {}),
            "world_cup_context_correction": feature_vector.get("world_cup_context_correction", {}),
            "player_form_snapshots_used": (
                int(home.get("key_player_form_summary", {}).get("snapshots_used", 0))
                + int(away.get("key_player_form_summary", {}).get("snapshots_used", 0))
            ),
            "team_strength_source": "team_strength_snapshots",
        },
    }
    if scoreline_payload is not None:
        prediction["expected_goals"] = scoreline_payload["expected_goals"]
        prediction["scoreline_model"] = scoreline_payload["scoreline_model"]
        prediction["scoreline_distribution"] = scoreline_payload["scoreline_distribution"]
        prediction["recommended_scores"] = scoreline_payload["recommended_scores"]
    # ponytail: candidate weights are internal training outputs, not public contract payloads.
    if validate_contract:
        validate_pre_match_prediction(prediction)
    return prediction


def _routed_probabilities(
    *,
    feature_vector: dict[str, Any],
    components: list[dict[str, Any]],
    legacy_probabilities: dict[str, float],
    probability_model_mode: str,
    scoreline_params: dict[str, Any] | None,
) -> tuple[dict[str, float], dict[str, Any], dict[str, Any] | None]:
    probabilities = dict(legacy_probabilities)
    routing = {
        "1x2": {
            "route": PROBABILITY_MODEL_LEGACY,
            "status": "available",
            "probability_keys": ["home_win", "draw", "away_win"],
        },
        "totals": {
            "route": "independent_poisson",
            "status": "unavailable",
            "reason_code": "scoreline_route_disabled",
            "probability_keys": ["over_2_5", "under_2_5"],
        },
        "btts": {
            "route": "independent_poisson",
            "status": "unavailable",
            "reason_code": "scoreline_route_disabled",
            "probability_keys": ["btts_yes", "btts_no"],
        },
        "scoreline": {
            "route": "independent_poisson",
            "status": "unavailable",
            "reason_code": "scoreline_route_disabled",
        },
    }
    if probability_model_mode == PROBABILITY_MODEL_LEGACY:
        return probabilities, routing, None

    unavailable_reason = _scoreline_route_unavailable_reason(feature_vector, components)
    if unavailable_reason is not None:
        for market in ("totals", "btts", "scoreline"):
            routing[market]["reason_code"] = unavailable_reason
        return probabilities, routing, None

    try:
        expected_goals = infer_expected_goals(feature_vector, components)
        params = scoreline_params or {}
        scoreline_model = build_scoreline_distribution(
            float(expected_goals["home_expected_goals"]),
            float(expected_goals["away_expected_goals"]),
            rho=float(params.get("rho") or 0.0),
        )
    except (ExpectedGoalsError, ScorelineModelError, TypeError, ValueError) as exc:
        reason_code = f"scoreline_route_error:{type(exc).__name__}"
        for market in ("totals", "btts", "scoreline"):
            routing[market]["reason_code"] = reason_code
        return probabilities, routing, None

    poisson_probabilities = dict(scoreline_model["probabilities"])
    probabilities.update({
        "over_2_5": float(poisson_probabilities["over_2_5"]),
        "under_2_5": float(poisson_probabilities["under_2_5"]),
        "btts_yes": float(poisson_probabilities["btts_yes"]),
        "btts_no": float(poisson_probabilities["btts_no"]),
    })
    for market in ("totals", "btts", "scoreline"):
        routing[market]["status"] = "available"
        routing[market].pop("reason_code", None)
    routing["scoreline"]["model_version"] = scoreline_model["version"]
    routing["scoreline"]["family"] = scoreline_model["family"]
    return probabilities, routing, {
        "expected_goals": expected_goals,
        "scoreline_model": {
            "version": scoreline_model["version"],
            "family": scoreline_model["family"],
            "rho": scoreline_model["rho"],
            "home_expected_goals": scoreline_model["home_expected_goals"],
            "away_expected_goals": scoreline_model["away_expected_goals"],
        },
        "scoreline_distribution": scoreline_model["scoreline_distribution"],
        "recommended_scores": scoreline_model["recommended_scores"],
    }


def _scoreline_route_unavailable_reason(
    feature_vector: dict[str, Any],
    components: list[dict[str, Any]],
) -> str | None:
    attack_defense = next(
        (
            item
            for item in components
            if str(item.get("dimension") or "") == "attack_defense_efficiency"
        ),
        None,
    )
    if not isinstance(attack_defense, dict) or str(attack_defense.get("status") or "") in {
        "unavailable",
        "blocked",
        "neutral_default",
    }:
        return "attack_defense_inputs_unavailable"
    team_features = feature_vector.get("team_features")
    if not isinstance(team_features, dict):
        return "team_features_unavailable"
    for side in ("home", "away"):
        features = team_features.get(side)
        if not isinstance(features, dict):
            return f"{side}_team_features_unavailable"
        for primary, fallback in (
            ("shrunk_goals_for", "goals_for_per_match"),
            ("shrunk_goals_against", "goals_against_per_match"),
        ):
            if not _positive_probability_input(features.get(primary), features.get(fallback)):
                return f"{side}_{fallback}_unavailable"
    return None


def _positive_probability_input(primary: Any, fallback: Any) -> bool:
    for value in (primary, fallback):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            return True
    return False


def validate_pre_match_prediction(prediction: dict[str, Any]) -> None:
    required = {
        "version",
        "fixture_id",
        "match_id",
        "home_team",
        "away_team",
        "generated_at",
        "as_of",
        "weights_version",
        "not_used_in_production_scoring_by_default",
        "team_scores",
        "probabilities",
        "probability_model_mode",
        "prediction_routing",
        "risk",
        "components",
        "coverage",
    }
    missing = sorted(required - set(prediction))
    if missing:
        raise PreMatchResearchScoringError(
            f"pre_match_prediction missing required keys: {', '.join(missing)}"
        )
    if prediction["version"] != P0_15_VERSION:
        raise PreMatchResearchScoringError("pre_match_prediction version must be p0.15")
    if prediction["weights_version"] != P0_15_WEIGHTS_VERSION:
        raise PreMatchResearchScoringError(
            "pre_match_prediction weights_version must be p0.15-research-preview"
        )
    if prediction["not_used_in_production_scoring_by_default"] is not True:
        raise PreMatchResearchScoringError(
            "pre_match_prediction must be marked as not used in production scoring"
        )
    components = prediction["components"]
    if not isinstance(components, list) or len(components) != len(P0_15_COMPONENT_DIMENSIONS):
        raise PreMatchResearchScoringError("pre_match_prediction must contain 8 components")
    dimensions = [str(item.get("dimension")) for item in components]
    if tuple(dimensions) != P0_15_COMPONENT_DIMENSIONS:
        raise PreMatchResearchScoringError("pre_match_prediction component dimensions drifted")
    for component in components:
        _validate_component(component)
    if prediction["probability_model_mode"] not in {
        PROBABILITY_MODEL_LEGACY,
        PROBABILITY_MODEL_ROUTED,
    }:
        raise PreMatchResearchScoringError("probability_model_mode is invalid")
    probabilities = prediction["probabilities"]
    total = probabilities["home_win"] + probabilities["draw"] + probabilities["away_win"]
    if abs(total - 1.0) > 0.02:
        raise PreMatchResearchScoringError(
            f"1x2 probabilities must sum to 1.0, got {total:.4f}"
        )
    for key in ("over_2_5", "upset_risk"):
        if not 0 <= probabilities[key] <= 1:
            raise PreMatchResearchScoringError(f"{key} must be in 0..1")
    routing = prediction.get("prediction_routing")
    if not isinstance(routing, dict) or set(routing) != {"1x2", "totals", "btts", "scoreline"}:
        raise PreMatchResearchScoringError("prediction_routing must expose 1x2, totals, btts, and scoreline")
    if routing["1x2"].get("route") != PROBABILITY_MODEL_LEGACY:
        raise PreMatchResearchScoringError("1x2 route must be legacy_logistic")
    for market in ("totals", "btts", "scoreline"):
        if routing[market].get("route") != "independent_poisson":
            raise PreMatchResearchScoringError(f"{market} route must be independent_poisson")
        if routing[market].get("status") not in {"available", "unavailable"}:
            raise PreMatchResearchScoringError(f"{market} route status is invalid")
    scoreline_available = routing["scoreline"].get("status") == "available"
    if scoreline_available:
        if not prediction.get("scoreline_distribution") or not prediction.get("recommended_scores"):
            raise PreMatchResearchScoringError("available scoreline route requires model distribution")
        for key in ("under_2_5", "btts_yes", "btts_no"):
            if key not in probabilities or not 0 <= probabilities[key] <= 1:
                raise PreMatchResearchScoringError(f"{key} must be available in 0..1")
    if prediction["risk"]["level"] not in {"low", "medium", "high"}:
        raise PreMatchResearchScoringError("risk.level must be low, medium, or high")
    if not 0 <= prediction["risk"]["confidence"] <= 100:
        raise PreMatchResearchScoringError("risk.confidence must be in 0..100")


def _validate_component(component: dict[str, Any]) -> None:
    required = {
        "dimension",
        "value",
        "home_value",
        "away_value",
        "status",
        "quality_score",
        "source_fields",
        "missing_reason",
    }
    missing = sorted(required - set(component))
    if missing:
        raise PreMatchResearchScoringError(
            f"component {component.get('dimension')} missing keys: {', '.join(missing)}"
        )
    if component["status"] not in P0_15_COMPONENT_STATUS_VALUES:
        raise PreMatchResearchScoringError(
            f"invalid component status for {component['dimension']}: {component['status']}"
        )
    if not 0 <= float(component["quality_score"]) <= 1:
        raise PreMatchResearchScoringError(
            f"component quality out of range for {component['dimension']}"
        )
    if not isinstance(component["source_fields"], list):
        raise PreMatchResearchScoringError(
            f"component source_fields must be list for {component['dimension']}"
        )


def _validate_feature_vector(feature_vector: dict[str, Any]) -> None:
    required = {
        "version",
        "fixture_id",
        "match_id",
        "home_team",
        "away_team",
        "generated_at",
        "as_of",
        "match",
        "team_features",
        "odds",
        "not_used_in_production_scoring_by_default",
    }
    missing = sorted(required - set(feature_vector))
    if missing:
        raise PreMatchResearchScoringError(
            f"feature_vector missing required keys: {', '.join(missing)}"
        )
    if feature_vector["version"] != P0_15_VERSION:
        raise PreMatchResearchScoringError("feature_vector version must be p0.15")
    if feature_vector["not_used_in_production_scoring_by_default"] is not True:
        raise PreMatchResearchScoringError(
            "feature_vector must be marked as not used in production scoring"
        )
    team_features = feature_vector["team_features"]
    if not isinstance(team_features, dict) or not isinstance(team_features.get("home"), dict):
        raise PreMatchResearchScoringError("feature_vector.team_features.home must exist")
    if not isinstance(team_features.get("away"), dict):
        raise PreMatchResearchScoringError("feature_vector.team_features.away must exist")


def _team_strength_component(home: dict[str, Any], away: dict[str, Any]) -> dict[str, Any]:
    return _side_feature_component(
        "team_strength",
        home,
        away,
        "team_strength_score",
        ["team_strength_snapshots.strength_value"],
    )


def _recent_form_component(home: dict[str, Any], away: dict[str, Any]) -> dict[str, Any]:
    home_value = _average_available([
        ppg_to_score(home.get("recent_points_per_game")),
        goal_diff_to_score(home.get("last_5_goal_diff")),
        ratio_to_score(home.get("unbeaten_rate")),
    ])
    away_value = _average_available([
        ppg_to_score(away.get("recent_points_per_game")),
        goal_diff_to_score(away.get("last_5_goal_diff")),
        ratio_to_score(away.get("unbeaten_rate")),
    ])
    home_value = _friendly_heavy_recent_form_value(home_value, home.get("friendly_match_ratio"))
    away_value = _friendly_heavy_recent_form_value(away_value, away.get("friendly_match_ratio"))
    return _component(
        "recent_form",
        home_value,
        away_value,
        _merge_status(home, away, "recent_form"),
        _merge_quality(home, away, "recent_form"),
        [
            "match_results.recent_points_per_game",
            "match_results.last_5_goal_diff",
            "match_results.unbeaten_rate",
            "match_results.friendly_match_ratio",
        ],
        _merge_missing_reason(home, away, "recent_form"),
    )


def _attack_defense_component(home: dict[str, Any], away: dict[str, Any]) -> dict[str, Any]:
    home_value = _average_available([
        positive_rate_score(home.get("goals_for_per_match"), 25, 25),
        inverse_rate_score(home.get("goals_against_per_match"), 25, 90),
    ])
    away_value = _average_available([
        positive_rate_score(away.get("goals_for_per_match"), 25, 25),
        inverse_rate_score(away.get("goals_against_per_match"), 25, 90),
    ])
    return _component(
        "attack_defense_efficiency",
        home_value,
        away_value,
        _merge_status(home, away, "attack_defense_efficiency"),
        _merge_quality(home, away, "attack_defense_efficiency"),
        [
            "match_results.goals_for_per_match",
            "match_results.goals_against_per_match",
        ],
        _merge_missing_reason(home, away, "attack_defense_efficiency"),
    )


def _schedule_fatigue_component(home: dict[str, Any], away: dict[str, Any]) -> dict[str, Any]:
    return _side_feature_component(
        "schedule_fatigue",
        home,
        away,
        "schedule_fatigue_score",
        ["match_results.rest_days"],
    )


def _key_player_status_component(home: dict[str, Any], away: dict[str, Any]) -> dict[str, Any]:
    source_fields = [
        "player_form_snapshots.club_recent_matches",
        "player_form_snapshots.club_recent_minutes",
        "player_form_snapshots.club_recent_starts",
        "player_form_snapshots.national_recent_caps",
        "player_form_snapshots.national_recent_minutes",
        "player_form_snapshots.national_recent_starts",
        "player_match_appearances.starter",
        "player_match_appearances.minutes_played",
        "player_match_appearances.played_at",
    ]
    if home.get("key_player_news_context") or away.get("key_player_news_context"):
        source_fields.extend([
            "pre_match_crawler_snapshot.injury_status",
            "pre_match_crawler_snapshot.key_player_status",
            "pre_match_news_snapshot.injury_status",
            "pre_match_news_snapshot.key_player_status",
        ])
    return _side_feature_component(
        "key_player_status",
        home,
        away,
        "key_player_form_score",
        source_fields,
    )


def _side_feature_component(
    dimension: str,
    home: dict[str, Any],
    away: dict[str, Any],
    field: str,
    source_fields: list[str],
) -> dict[str, Any]:
    return _component(
        dimension,
        _score_or_neutral(home.get(field)),
        _score_or_neutral(away.get(field)),
        _merge_status(home, away, dimension),
        _merge_quality(home, away, dimension),
        source_fields,
        _merge_missing_reason(home, away, dimension),
    )


def _odds_movement_component(feature_vector: dict[str, Any]) -> dict[str, Any]:
    odds = feature_vector["odds"]
    return _component(
        "odds_movement",
        _score_or_neutral(odds.get("home_score")),
        _score_or_neutral(odds.get("away_score")),
        str(odds.get("status") or "unavailable"),
        round(float(odds.get("quality_score") or 0.0), 3),
        list(odds.get("source_fields") or []),
        odds.get("missing_reason"),
    )


def _lineup_integrity_component(feature_vector: dict[str, Any]) -> dict[str, Any]:
    home = feature_vector["team_features"]["home"]
    away = feature_vector["team_features"]["away"]
    crawler_snapshot = feature_vector.get("pre_match_crawler_snapshot", {})
    news_snapshot = feature_vector.get("pre_match_news_snapshot", {})
    status = _allowed_status(_merge_status(home, away, "lineup_integrity"))
    quality = _merge_quality(home, away, "lineup_integrity")
    missing_reason = _merge_missing_reason(home, away, "lineup_integrity")
    if status == "unavailable":
        missing_reason = missing_reason or "pre_match_lineup_news_unavailable"
    return _component(
        "lineup_integrity",
        _score_or_neutral(home.get("lineup_integrity_score")),
        _score_or_neutral(away.get("lineup_integrity_score")),
        status,
        quality,
        [
            "pre_match_crawler_snapshot.lineup_status",
            "pre_match_crawler_snapshot.items",
            "pre_match_crawler_snapshot.source_summary",
            "pre_match_news_snapshot.lineup_status",
            "pre_match_news_snapshot.squad_status",
            "pre_match_news_snapshot.items",
            "pre_match_news_snapshot.sources",
        ],
        missing_reason,
    ) | {
        "news_snapshot_id": news_snapshot.get("snapshot_id"),
        "crawler_snapshot_id": crawler_snapshot.get("snapshot_id"),
        "home_source_tier": home.get("lineup_integrity_source_tier"),
        "away_source_tier": away.get("lineup_integrity_source_tier"),
        "home_raw_score": _score_or_neutral(home.get("lineup_integrity_raw_score")),
        "away_raw_score": _score_or_neutral(away.get("lineup_integrity_raw_score")),
        "home_source_confidence": float(home.get("lineup_integrity_source_confidence") or 0.0),
        "away_source_confidence": float(away.get("lineup_integrity_source_confidence") or 0.0),
    }


def _motivation_stage_component(feature_vector: dict[str, Any]) -> dict[str, Any]:
    context = feature_vector.get("motivation_context") or {}
    status = _allowed_status(str(context.get("status") or "partial"))
    return _component(
        "motivation_stage",
        _score_or_neutral(context.get("motivation_score_home")),
        _score_or_neutral(context.get("motivation_score_away")),
        status,
        float(context.get("quality_score") or 0.0),
        [
            "pre_match_crawler_snapshot.motivation_status",
            "pre_match_crawler_snapshot.items",
            "motivation_context.rules_applied",
            "motivation_context.reason_codes",
            "motivation_context.stage",
        ],
        None if context.get("rules_applied") else "motivation_context_insufficient",
    ) | {
        "motivation_context": context,
    }


def _neutral_component(
    dimension: str,
    missing_reason: str,
    source_fields: list[str],
) -> dict[str, Any]:
    return _component(
        dimension,
        50.0,
        50.0,
        "neutral_default",
        0.0,
        source_fields,
        missing_reason,
    )


def _component(
    dimension: str,
    home_value: float,
    away_value: float,
    status: str,
    quality_score: float,
    source_fields: list[str],
    missing_reason: str | None,
) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "value": round(home_value - away_value, 2),
        "home_value": round(clamp(home_value), 2),
        "away_value": round(clamp(away_value), 2),
        "status": status,
        "quality_score": round(clamp(float(quality_score), 0.0, 1.0), 3),
        "source_fields": source_fields,
        "missing_reason": missing_reason,
    }


def _weighted_team_scores(
    components: list[dict[str, Any]],
    *,
    weights: dict[str, float] | None = None,
) -> tuple[float, float]:
    weight_map = _resolved_weights(weights)
    home_total = 0.0
    away_total = 0.0
    for component in components:
        weight = weight_map[str(component["dimension"])]
        home_total += float(component["home_value"]) * weight
        away_total += float(component["away_value"]) * weight
    return round(home_total, 2), round(away_total, 2)


def _probabilities(
    home_score: float,
    away_score: float,
    components: list[dict[str, Any]],
    feature_vector: dict[str, Any],
    *,
    probability_params: dict[str, float] | None = None,
) -> dict[str, float]:
    params = _resolved_probability_params(probability_params)
    gap = home_score - away_score
    home_raw = 1 / (1 + math.exp(-gap / params["gap_divisor"]))
    draw = max(
        params["draw_min"],
        min(params["draw_max"], params["draw_base"] - abs(gap) * params["draw_slope"]),
    )
    home_win = home_raw * (1 - draw)
    away_win = (1 - home_raw) * (1 - draw)
    attack = next(item for item in components if item["dimension"] == "attack_defense_efficiency")
    attack_avg = (attack["home_value"] + attack["away_value"]) / 2
    total_line = feature_vector["odds"].get("total_goals_line")
    odds_total_adjustment = 0.0
    if total_line is not None:
        odds_total_adjustment = (float(total_line) - 2.5) * params["over_odds_scale"]
    over_2_5 = clamp(
        params["over_base"] + (attack_avg - 50) / params["over_attack_scale"] + odds_total_adjustment,
        params["over_min"],
        params["over_max"],
    )
    upset_risk = clamp(
        params["upset_base"] + max(0, params["upset_gap_threshold"] - abs(gap)) / params["upset_gap_scale"],
        params["upset_min"],
        params["upset_max"],
    )
    return {
        "home_win": round(home_win, 3),
        "draw": round(draw, 3),
        "away_win": round(away_win, 3),
        "over_2_5": round(over_2_5, 3),
        "upset_risk": round(upset_risk, 3),
    }


def _risk(
    score_gap: float,
    components: list[dict[str, Any]],
    probabilities: dict[str, float],
) -> dict[str, Any]:
    reason_codes: list[str] = []
    risk_points = 0
    if abs(score_gap) < 5:
        risk_points += 1
        reason_codes.append("close_score_gap")
    for component in components:
        status = component["status"]
        dimension = component["dimension"]
        if status == "blocked":
            risk_points += 2
            reason_codes.append(f"{dimension}_blocked")
        elif status in {"partial", "unavailable"}:
            risk_points += 1
            reason_codes.append(f"{dimension}_{status}")
        elif status == "neutral_default":
            reason_codes.append(f"{dimension}_neutral_default")
    if probabilities["upset_risk"] > 0.32:
        risk_points += 1
        reason_codes.append("upset_risk_high")
    data_quality = _mean([float(item["quality_score"]) for item in components])
    confidence = 72 + abs(score_gap) * 0.7 - risk_points * 5.5 - (1 - data_quality) * 20
    confidence = round(clamp(confidence, 0, 100), 2)
    if confidence < 45:
        risk_points += 1
        reason_codes.append("low_confidence")
    level = "low" if risk_points == 0 else "medium" if risk_points <= 3 else "high"
    return {
        "level": level,
        "confidence": confidence,
        "reason_codes": sorted(set(reason_codes)),
        "data_quality_score": round(data_quality, 3),
    }


def _coverage(components: list[dict[str, Any]]) -> dict[str, Any]:
    blocked = [item["dimension"] for item in components if item["status"] == "blocked"]
    unavailable = [item["dimension"] for item in components if item["status"] == "unavailable"]
    neutral = [item["dimension"] for item in components if item["status"] == "neutral_default"]
    partial = [item["dimension"] for item in components if item["status"] == "partial"]
    if blocked:
        status = "blocked"
    elif unavailable or neutral or partial:
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status,
        "missing_components": blocked,
        "unavailable_components": unavailable,
        "neutral_default_components": neutral,
        "partial_components": partial,
        "ok_components": [item["dimension"] for item in components if item["status"] == "ok"],
    }


def _merge_status(home: dict[str, Any], away: dict[str, Any], dimension: str) -> str:
    statuses = [
        str(home.get("component_status", {}).get(dimension) or "unavailable"),
        str(away.get("component_status", {}).get(dimension) or "unavailable"),
    ]
    if "blocked" in statuses:
        return "blocked"
    if "unavailable" in statuses:
        return "unavailable" if statuses.count("unavailable") == 2 else "partial"
    if "partial" in statuses:
        return "partial"
    if "stale" in statuses:
        return "partial"
    if "neutral_default" in statuses:
        return "neutral_default"
    return "ok"


def _merge_quality(home: dict[str, Any], away: dict[str, Any], dimension: str) -> float:
    values = [
        float(home.get("component_quality", {}).get(dimension) or 0.0),
        float(away.get("component_quality", {}).get(dimension) or 0.0),
    ]
    return round(sum(values) / len(values), 3)


def _merge_missing_reason(home: dict[str, Any], away: dict[str, Any], dimension: str) -> str | None:
    reasons = [
        reason
        for reason in (
            home.get("missing_reasons", {}).get(dimension),
            away.get("missing_reasons", {}).get(dimension),
        )
        if reason
    ]
    return "; ".join(sorted(set(str(reason) for reason in reasons))) or None


def _allowed_status(status: str) -> str:
    if status == "stale":
        return "partial"
    if status in P0_15_COMPONENT_STATUS_VALUES:
        return status
    return "unavailable"


def _score_or_neutral(value: Any) -> float:
    if value is None:
        return 50.0
    return float(value)


def _average_available(values: list[float | None]) -> float:
    available = [float(value) for value in values if value is not None]
    if not available:
        return 50.0
    return round(sum(available) / len(available), 2)


def _friendly_heavy_recent_form_value(value: float, friendly_match_ratio: Any) -> float:
    if friendly_match_ratio is None or float(friendly_match_ratio) < 0.8:
        return value
    return round(50 + (float(value) - 50) * 0.5, 2)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _resolved_weights(weights: dict[str, float] | None) -> dict[str, float]:
    if weights is None:
        return P0_15_RESEARCH_WEIGHTS
    resolved = {
        dimension: float(weights[dimension])
        for dimension in P0_15_COMPONENT_DIMENSIONS
    }
    total = sum(resolved.values()) or 1.0
    return {
        dimension: resolved[dimension] / total
        for dimension in P0_15_COMPONENT_DIMENSIONS
    }


def _resolved_probability_params(probability_params: dict[str, float] | None) -> dict[str, float]:
    if probability_params is None:
        return P0_15_PROBABILITY_PARAMS
    resolved = dict(P0_15_PROBABILITY_PARAMS)
    for key, value in probability_params.items():
        if key in resolved:
            resolved[key] = float(value)
    return resolved
