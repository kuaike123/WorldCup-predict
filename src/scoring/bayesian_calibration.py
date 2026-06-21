from __future__ import annotations

from collections import Counter
from typing import Any


CALIBRATION_SCHEMA_VERSION = "bayesian_calibration.v1"
OFFICIAL_WORLD_CUP_SCOPE = "official_world_cup"
FRIENDLY_SAMPLE_SCOPE = "pre_tournament_friendly"
OUTCOMES = ("home_win", "draw", "away_win")
DEFAULT_MINIMUM_OFFICIAL_SAMPLES = 20
DEFAULT_DIRICHLET_PRIOR_STRENGTH = 24.0
DEFAULT_BETA_PRIOR_STRENGTH = 20.0


def build_bayesian_calibration(
    prediction: dict[str, Any],
    reviews: list[dict[str, Any]] | None,
    *,
    exclude_match_id: str | None = None,
    minimum_official_samples: int = DEFAULT_MINIMUM_OFFICIAL_SAMPLES,
    dirichlet_prior_strength: float = DEFAULT_DIRICHLET_PRIOR_STRENGTH,
    beta_prior_strength: float = DEFAULT_BETA_PRIOR_STRENGTH,
) -> dict[str, Any]:
    baseline = _baseline_probabilities(prediction)
    if baseline is None:
        return _not_applied(
            baseline={},
            reason_codes=["baseline_probabilities_missing"],
            official_samples=[],
            friendly_samples=[],
            ignored_samples=[],
            minimum_official_samples=minimum_official_samples,
            dirichlet_prior_strength=dirichlet_prior_strength,
            beta_prior_strength=beta_prior_strength,
            exclude_match_id=exclude_match_id,
        )

    official_samples, friendly_samples, ignored_samples = _review_samples(
        reviews or [],
        exclude_match_id=exclude_match_id,
    )
    effective_official_sample_count = round(
        sum(float(sample["calibration_sample_weight"]) for sample in official_samples),
        4,
    )
    reason_codes: list[str] = []
    if len(official_samples) < minimum_official_samples:
        reason_codes.append("insufficient_official_world_cup_samples")
    if effective_official_sample_count < float(minimum_official_samples):
        reason_codes.append("insufficient_effective_official_world_cup_samples")
    if reason_codes:
        if friendly_samples:
            reason_codes.append("friendly_samples_observed_not_applied")
        return _not_applied(
            baseline=baseline,
            reason_codes=reason_codes,
            official_samples=official_samples,
            friendly_samples=friendly_samples,
            ignored_samples=ignored_samples,
            minimum_official_samples=minimum_official_samples,
            dirichlet_prior_strength=dirichlet_prior_strength,
            beta_prior_strength=beta_prior_strength,
            exclude_match_id=exclude_match_id,
        )

    outcome_counts: Counter[str] = Counter()
    over_successes = 0.0
    upset_successes = 0.0
    for sample in official_samples:
        weight = float(sample["calibration_sample_weight"])
        outcome_counts[str(sample["actual_outcome"])] += weight
        over_successes += weight if sample["over_2_5_result"] else 0.0
        upset_successes += weight if sample["upset_occurred"] else 0.0

    calibrated_1x2, posterior_1x2 = _dirichlet_posterior_mean(
        baseline,
        outcome_counts,
        prior_strength=dirichlet_prior_strength,
    )
    calibrated_over, posterior_over = _beta_posterior_mean(
        float(baseline["over_2_5"]),
        success_weight=over_successes,
        total_weight=effective_official_sample_count,
        prior_strength=beta_prior_strength,
    )
    calibrated_upset, posterior_upset = _beta_posterior_mean(
        float(baseline["upset_risk"]),
        success_weight=upset_successes,
        total_weight=effective_official_sample_count,
        prior_strength=beta_prior_strength,
    )
    calibrated_probabilities = _rounded_calibrated_probabilities(
        calibrated_1x2,
        over_2_5=calibrated_over,
        upset_risk=calibrated_upset,
    )
    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "status": "applied",
        "applied": True,
        "reason_codes": ["official_world_cup_samples_applied"],
        "sample_scope": OFFICIAL_WORLD_CUP_SCOPE,
        "minimum_official_samples": minimum_official_samples,
        "official_sample_count": len(official_samples),
        "effective_official_sample_count": effective_official_sample_count,
        "friendly_sample_count": len(friendly_samples),
        "ignored_sample_count": len(ignored_samples),
        "baseline_probabilities": baseline,
        "calibrated_probabilities": calibrated_probabilities,
        "posterior": {
            "one_x_two": posterior_1x2,
            "over_2_5": posterior_over,
            "upset_risk": posterior_upset,
        },
        "coverage": _coverage_summary(
            official_samples=official_samples,
            friendly_samples=friendly_samples,
            ignored_samples=ignored_samples,
            exclude_match_id=exclude_match_id,
        ),
        "notes": [
            "Baseline probabilities remain unchanged under prediction.probabilities.",
            "Only official World Cup review samples are allowed to move the posterior.",
            "Friendly samples are audit-visible but do not change calibrated probabilities.",
        ],
    }


def _not_applied(
    *,
    baseline: dict[str, float],
    reason_codes: list[str],
    official_samples: list[dict[str, Any]],
    friendly_samples: list[dict[str, Any]],
    ignored_samples: list[dict[str, Any]],
    minimum_official_samples: int,
    dirichlet_prior_strength: float,
    beta_prior_strength: float,
    exclude_match_id: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "status": "not_applied",
        "applied": False,
        "reason_codes": reason_codes,
        "sample_scope": OFFICIAL_WORLD_CUP_SCOPE,
        "minimum_official_samples": minimum_official_samples,
        "official_sample_count": len(official_samples),
        "effective_official_sample_count": round(
            sum(float(sample["calibration_sample_weight"]) for sample in official_samples),
            4,
        ),
        "friendly_sample_count": len(friendly_samples),
        "ignored_sample_count": len(ignored_samples),
        "baseline_probabilities": baseline,
        "calibrated_probabilities": baseline,
        "posterior": {
            "one_x_two": {
                "prior_strength": dirichlet_prior_strength,
                "outcome_counts": {outcome: 0.0 for outcome in OUTCOMES},
                "posterior_alpha": {
                    outcome: round(float(baseline.get(outcome, 0.0)) * dirichlet_prior_strength, 6)
                    for outcome in OUTCOMES
                },
            },
            "over_2_5": {
                "prior_strength": beta_prior_strength,
                "success_weight": 0.0,
                "failure_weight": 0.0,
                "posterior_alpha": round(float(baseline.get("over_2_5", 0.0)) * beta_prior_strength, 6),
                "posterior_beta": round((1.0 - float(baseline.get("over_2_5", 0.0))) * beta_prior_strength, 6),
            },
            "upset_risk": {
                "prior_strength": beta_prior_strength,
                "success_weight": 0.0,
                "failure_weight": 0.0,
                "posterior_alpha": round(float(baseline.get("upset_risk", 0.0)) * beta_prior_strength, 6),
                "posterior_beta": round((1.0 - float(baseline.get("upset_risk", 0.0))) * beta_prior_strength, 6),
            },
        },
        "coverage": _coverage_summary(
            official_samples=official_samples,
            friendly_samples=friendly_samples,
            ignored_samples=ignored_samples,
            exclude_match_id=exclude_match_id,
        ),
        "notes": [
            "Baseline probabilities remain unchanged under prediction.probabilities.",
            "Calibration safely falls back when official World Cup sample coverage is insufficient.",
        ],
    }


def _coverage_summary(
    *,
    official_samples: list[dict[str, Any]],
    friendly_samples: list[dict[str, Any]],
    ignored_samples: list[dict[str, Any]],
    exclude_match_id: str | None,
) -> dict[str, Any]:
    return {
        "excluded_match_id": exclude_match_id,
        "official_match_ids": [sample["match_id"] for sample in official_samples[:20]],
        "friendly_match_ids": [sample["match_id"] for sample in friendly_samples[:20]],
        "ignored_match_ids": [sample["match_id"] for sample in ignored_samples[:20]],
    }


def _baseline_probabilities(prediction: dict[str, Any]) -> dict[str, float] | None:
    probabilities = prediction.get("probabilities")
    if not isinstance(probabilities, dict):
        return None
    try:
        baseline = {
            "home_win": float(probabilities["home_win"]),
            "draw": float(probabilities["draw"]),
            "away_win": float(probabilities["away_win"]),
            "over_2_5": float(probabilities["over_2_5"]),
            "upset_risk": float(probabilities["upset_risk"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "home_win": round(baseline["home_win"], 3),
        "draw": round(baseline["draw"], 3),
        "away_win": round(baseline["away_win"], 3),
        "over_2_5": round(_clamp_probability(baseline["over_2_5"]), 3),
        "upset_risk": round(_clamp_probability(baseline["upset_risk"]), 3),
    }


def _review_samples(
    reviews: list[dict[str, Any]],
    *,
    exclude_match_id: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    official_samples: list[dict[str, Any]] = []
    friendly_samples: list[dict[str, Any]] = []
    ignored_samples: list[dict[str, Any]] = []
    for review in reviews:
        sample = _review_sample(review, exclude_match_id=exclude_match_id)
        if sample is None:
            continue
        if sample["sample_scope"] == OFFICIAL_WORLD_CUP_SCOPE and sample["official_world_cup_match"]:
            official_samples.append(sample)
        elif sample["sample_scope"] == FRIENDLY_SAMPLE_SCOPE:
            friendly_samples.append(sample)
        else:
            ignored_samples.append(sample)
    return official_samples, friendly_samples, ignored_samples


def _review_sample(review: dict[str, Any], *, exclude_match_id: str | None) -> dict[str, Any] | None:
    match_id = str(review.get("match_id") or "")
    if exclude_match_id and match_id == exclude_match_id:
        return None
    actual_result = review.get("actual_result")
    if not isinstance(actual_result, dict):
        return None
    actual_outcome = str(actual_result.get("actual_outcome") or "")
    if actual_outcome not in OUTCOMES:
        return None
    over_2_5_result = actual_result.get("over_2_5_result")
    if not isinstance(over_2_5_result, bool):
        return None
    provenance = dict(review.get("sample_provenance") or {})
    sample_scope = str(provenance.get("sample_scope") or review.get("sample_scope") or "").strip()
    official_flag = provenance.get("official_world_cup_match")
    if not sample_scope and official_flag is None:
        return None
    if not sample_scope:
        sample_scope = OFFICIAL_WORLD_CUP_SCOPE if bool(official_flag) else "general_match"
    official_world_cup_match = bool(official_flag) if official_flag is not None else sample_scope == OFFICIAL_WORLD_CUP_SCOPE
    if sample_scope == FRIENDLY_SAMPLE_SCOPE:
        official_world_cup_match = False
    weight = _clamp_probability(
        float(provenance.get("calibration_sample_weight") or (1.0 if official_world_cup_match else 0.0))
    )
    favorite_outcome = _favorite_outcome(review)
    upset_occurred = (
        favorite_outcome in {"home_win", "away_win"}
        and actual_outcome in {"home_win", "away_win"}
        and favorite_outcome != actual_outcome
    )
    return {
        "match_id": match_id,
        "sample_scope": sample_scope,
        "official_world_cup_match": official_world_cup_match,
        "calibration_sample_weight": weight,
        "actual_outcome": actual_outcome,
        "over_2_5_result": over_2_5_result,
        "upset_occurred": upset_occurred,
    }


def _favorite_outcome(review: dict[str, Any]) -> str:
    evaluation = review.get("evaluation")
    if isinstance(evaluation, dict):
        favorite_outcome = str(evaluation.get("favorite_outcome") or "")
        if favorite_outcome in {"home_win", "away_win"}:
            return favorite_outcome
    snapshot = review.get("prediction_snapshot")
    probabilities = snapshot.get("probabilities") if isinstance(snapshot, dict) else None
    if not isinstance(probabilities, dict):
        return ""
    home = float(probabilities.get("home_win") or 0.0)
    away = float(probabilities.get("away_win") or 0.0)
    return "home_win" if home >= away else "away_win"


def _dirichlet_posterior_mean(
    baseline: dict[str, float],
    outcome_counts: Counter[str],
    *,
    prior_strength: float,
) -> tuple[dict[str, float], dict[str, Any]]:
    posterior_alpha = {
        outcome: float(baseline[outcome]) * prior_strength + float(outcome_counts.get(outcome, 0.0))
        for outcome in OUTCOMES
    }
    total = sum(posterior_alpha.values()) or 1.0
    posterior_mean = {
        outcome: posterior_alpha[outcome] / total
        for outcome in OUTCOMES
    }
    return posterior_mean, {
        "prior_strength": prior_strength,
        "outcome_counts": {
            outcome: round(float(outcome_counts.get(outcome, 0.0)), 4)
            for outcome in OUTCOMES
        },
        "posterior_alpha": {
            outcome: round(value, 6)
            for outcome, value in posterior_alpha.items()
        },
    }


def _beta_posterior_mean(
    baseline_probability: float,
    *,
    success_weight: float,
    total_weight: float,
    prior_strength: float,
) -> tuple[float, dict[str, Any]]:
    prior_alpha = _clamp_probability(baseline_probability) * prior_strength
    prior_beta = (1.0 - _clamp_probability(baseline_probability)) * prior_strength
    failure_weight = max(0.0, total_weight - success_weight)
    posterior_alpha = prior_alpha + success_weight
    posterior_beta = prior_beta + failure_weight
    posterior_mean = posterior_alpha / (posterior_alpha + posterior_beta or 1.0)
    return posterior_mean, {
        "prior_strength": prior_strength,
        "success_weight": round(success_weight, 4),
        "failure_weight": round(failure_weight, 4),
        "posterior_alpha": round(posterior_alpha, 6),
        "posterior_beta": round(posterior_beta, 6),
    }


def _rounded_calibrated_probabilities(
    one_x_two: dict[str, float],
    *,
    over_2_5: float,
    upset_risk: float,
) -> dict[str, float]:
    rounded = {outcome: round(_clamp_probability(one_x_two[outcome]), 3) for outcome in OUTCOMES}
    diff = round(1.0 - sum(rounded.values()), 3)
    if diff:
        largest = max(rounded, key=rounded.get)
        rounded[largest] = round(_clamp_probability(rounded[largest] + diff), 3)
    rounded["over_2_5"] = round(_clamp_probability(over_2_5), 3)
    rounded["upset_risk"] = round(_clamp_probability(upset_risk), 3)
    return rounded


def _clamp_probability(value: float) -> float:
    return max(0.0, min(float(value), 1.0))
