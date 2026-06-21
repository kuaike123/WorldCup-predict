from __future__ import annotations

from datetime import UTC, datetime
from math import sqrt
from typing import Any
from uuid import uuid4

from .config import load_weights


DISCLAIMER = "赛后复盘仅生成参数调整建议，不自动修改模型权重或产生投注指令。"
OUTCOMES = ("home_win", "draw", "away_win")
PRE_MATCH_FACTORS = (
    "team_strength",
    "recent_form",
    "lineup_integrity",
    "key_player_status",
    "attack_defense_efficiency",
    "schedule_fatigue",
    "motivation_stage",
    "odds_movement",
)
WEIGHT_BOUNDS = {
    "team_strength": (0.12, 0.28),
    "recent_form": (0.08, 0.22),
    "lineup_integrity": (0.08, 0.22),
    "key_player_status": (0.06, 0.20),
    "attack_defense_efficiency": (0.08, 0.24),
    "schedule_fatigue": (0.03, 0.14),
    "motivation_stage": (0.03, 0.16),
    "odds_movement": (0.05, 0.20),
}
CONFIDENCE_BY_ABNORMALITY = {
    "normal": 1.0,
    "none": 1.0,
    "minor_var_or_penalty": 0.8,
    "penalty_or_var": 0.8,
    "injury_or_var_changed_momentum": 0.5,
    "early_red_card": 0.3,
    "heavy_rotation_or_no_motivation": 0.2,
    "extreme_weather": 0.6,
    "garbage_time_distortion": 0.7,
}
OUTLIER_BY_ABNORMALITY = {
    "normal": 1.0,
    "none": 1.0,
    "penalty_or_var": 0.7,
    "minor_var_or_penalty": 0.7,
    "early_red_card": 0.4,
    "heavy_rotation_or_no_motivation": 0.5,
    "extreme_weather": 0.6,
    "garbage_time_distortion": 0.7,
}
UNKNOWN_ABNORMALITY_CONFIDENCE = 0.6
UNKNOWN_ABNORMALITY_OUTLIER = 0.7


def build_post_match_review(
    report: dict[str, Any],
    actual_payload: dict[str, Any],
    *,
    reviewed_match_count: int,
    live_context: dict[str, Any] | None = None,
    weights_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = weights_config or load_weights()
    reviewed_count = max(1, reviewed_match_count)
    eta_pre = round(max(0.01, 0.12 / sqrt(reviewed_count)), 4)
    eta_live = round(max(0.015, 0.18 / sqrt(reviewed_count)), 4)
    review_confidence = _review_confidence(actual_payload)
    outlier_factor = _outlier_factor(actual_payload)
    prediction = _prediction_snapshot(report, live_context or {})
    actual_result = _actual_result(actual_payload)
    evaluation = _evaluation(prediction, actual_result, actual_payload, review_confidence, outlier_factor)
    findings = _bias_findings(actual_payload, evaluation)
    proposals, live_rule_proposals, upset_rule_proposals, parking_lot = _proposals(
        findings,
        config,
        eta_pre=eta_pre,
        eta_live=eta_live,
        review_confidence=review_confidence,
        outlier_factor=outlier_factor,
    )

    return {
        "review_id": f"pmr_{uuid4().hex[:12]}",
        "match_id": str(report["match_id"]),
        "status": "proposal_pending_review",
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "weights_version": str(config["version"]),
        "reviewed_match_count": reviewed_count,
        "learning_rates": {
            "pre_match": eta_pre,
            "live_signal": eta_live,
        },
        "prediction_snapshot": prediction,
        "actual_result": actual_result,
        "evaluation": evaluation,
        "bias_findings": findings,
        "pre_match_weight_proposals": proposals,
        "live_signal_rule_proposals": live_rule_proposals,
        "upset_rule_proposals": upset_rule_proposals,
        "parking_lot_factors": parking_lot,
        "normalized_weights_preview": _normalized_weights_preview(config, proposals),
        "auto_applied": False,
        "admin_review_required": True,
        "disclaimer": DISCLAIMER,
    }


def _prediction_snapshot(report: dict[str, Any], live_context: dict[str, Any]) -> dict[str, Any]:
    analysis = _as_dict(report.get("analysis"))
    recommendation = _as_dict(report.get("recommendation"))
    probabilities = _as_dict(recommendation.get("probabilities") or analysis.get("probabilities"))
    snapshot = {
        "report_id": report.get("report_id"),
        "home_score": _number(analysis.get("home_score", report.get("home_score"))),
        "away_score": _number(analysis.get("away_score", report.get("away_score"))),
        "confidence": _number(report.get("confidence", analysis.get("confidence"))),
        "probabilities": probabilities,
        "market_signal": _as_dict(recommendation.get("market_signal") or analysis.get("market_signal")),
        "dimension_breakdown": analysis.get("dimension_breakdown", report.get("dimension_breakdown", [])),
    }
    if live_context:
        snapshot["live_context"] = live_context
    return snapshot


def _actual_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "final_score": dict(payload["final_score"]),
        "actual_outcome": payload["actual_outcome"],
        "over_2_5_result": bool(payload["over_2_5_result"]),
        "key_events": list(payload.get("key_events") or []),
        "match_stats": dict(payload.get("match_stats") or {}),
        "abnormality_flags": list(payload.get("abnormality_flags") or []),
        "review_tags": list(payload.get("review_tags") or []),
        "reviewer_note": payload.get("reviewer_note"),
    }


def _evaluation(
    prediction: dict[str, Any],
    actual_result: dict[str, Any],
    payload: dict[str, Any],
    review_confidence: float,
    outlier_factor: float,
) -> dict[str, Any]:
    probabilities = _as_dict(prediction.get("probabilities"))
    predicted_outcome = _predicted_outcome(probabilities)
    actual_outcome = str(actual_result["actual_outcome"])
    outcome_hit = predicted_outcome == actual_outcome
    over_predicted = _number(probabilities.get("over_2_5")) >= 0.5
    over_2_5_hit = over_predicted == bool(actual_result["over_2_5_result"])
    favorite_outcome, favorite_probability = _favorite_team_outcome(probabilities)
    favorite_lost = (
        (favorite_outcome == "home_win" and actual_outcome == "away_win")
        or (favorite_outcome == "away_win" and actual_outcome == "home_win")
    )
    upset_missed = favorite_probability >= 0.60 and favorite_lost and _number(probabilities.get("upset_risk")) < 0.25
    level = _prediction_error_level(
        outcome_hit=outcome_hit,
        upset_missed=upset_missed,
        favorite_probability=favorite_probability,
        prediction_confidence=_number(prediction.get("confidence")),
        tags=list(payload.get("review_tags") or []),
    )
    return {
        "predicted_outcome": predicted_outcome,
        "actual_outcome": actual_outcome,
        "outcome_hit": outcome_hit,
        "predicted_over_2_5": over_predicted,
        "over_2_5_hit": over_2_5_hit,
        "favorite_outcome": favorite_outcome,
        "favorite_probability": round(favorite_probability, 4),
        "upset_missed": upset_missed,
        "prediction_error_level": level,
        "review_confidence": review_confidence,
        "outlier_factor": outlier_factor,
        "primary_error_source": _primary_error_source(payload),
        "reason_codes": _evaluation_reason_codes(payload, outcome_hit, over_2_5_hit, upset_missed),
    }


def _bias_findings(payload: dict[str, Any], evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    tags = list(payload.get("review_tags") or [])
    if not tags:
        return [
            {
                "finding_id": "finding_needs_human_factor_tagging",
                "source_layer": "post_match",
                "factor": "needs_human_factor_tagging",
                "error_direction": 0,
                "bias_strength": 0.3,
                "severity": "low",
                "evidence": ["review_tags 缺失，本场不生成高置信数值调权 proposal。"],
                "proposal_target": "human_review",
            }
        ]

    findings: list[dict[str, Any]] = []
    for index, tag in enumerate(tags, start=1):
        factor = str(tag["factor"])
        source_layer = str(tag.get("source_layer") or "pre_match")
        target = _proposal_target(source_layer, factor)
        evidence = [
            f"predicted_outcome={evaluation['predicted_outcome']}, actual_outcome={evaluation['actual_outcome']}",
            str(tag["evidence"]),
        ]
        findings.append({
            "finding_id": f"finding_{index:02d}",
            "source_layer": source_layer,
            "factor": factor,
            "error_direction": int(tag["error_direction"]),
            "bias_strength": float(tag["bias_strength"]),
            "severity": _severity(float(tag["bias_strength"])),
            "evidence": evidence,
            "proposal_target": target,
        })
    return findings


def _proposals(
    findings: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    eta_pre: float,
    eta_live: float,
    review_confidence: float,
    outlier_factor: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    weights = dict(config["pre_match_weights"])
    pre_match: list[dict[str, Any]] = []
    live_rules: list[dict[str, Any]] = []
    upset_rules: list[dict[str, Any]] = []
    parking_lot: list[dict[str, Any]] = []

    for index, finding in enumerate(findings, start=1):
        factor = str(finding["factor"])
        if factor not in weights and factor != "needs_human_factor_tagging":
            parking_lot.append({
                "factor": factor,
                "source_layer": finding["source_layer"],
                "evidence": list(finding.get("evidence") or []),
                "reason": "当前权重配置不存在该 factor，P1 仅记录为后续模型扩展候选。",
            })

        if finding["proposal_target"] == "pre_match_weight" and factor in weights:
            proposal = _pre_match_weight_proposal(
                index,
                finding,
                weights,
                eta=eta_pre,
                review_confidence=review_confidence,
                outlier_factor=outlier_factor,
            )
            if proposal is not None:
                pre_match.append(proposal)
        elif finding["proposal_target"] == "live_signal_rule":
            live_rules.append(_rule_proposal(
                "lsprop",
                index,
                finding,
                eta=eta_live,
                review_confidence=review_confidence,
                outlier_factor=outlier_factor,
                rule_family="live_signal",
            ))
        elif finding["proposal_target"] == "upset_rule":
            upset_rules.append(_rule_proposal(
                "uprop",
                index,
                finding,
                eta=eta_live,
                review_confidence=review_confidence,
                outlier_factor=outlier_factor,
                rule_family="upset",
            ))

    return pre_match, live_rules, upset_rules, parking_lot


def _pre_match_weight_proposal(
    index: int,
    finding: dict[str, Any],
    weights: dict[str, Any],
    *,
    eta: float,
    review_confidence: float,
    outlier_factor: float,
) -> dict[str, Any] | None:
    direction = int(finding["error_direction"])
    if direction == 0:
        return None
    factor = str(finding["factor"])
    old_weight = round(float(weights[factor]), 6)
    delta_points = _delta_points(
        eta=eta,
        review_confidence=review_confidence,
        outlier_factor=outlier_factor,
        bias_strength=float(finding["bias_strength"]),
        error_direction=direction,
        limit=2.0,
    )
    delta_weight = round(delta_points / 100, 6)
    lower, upper = WEIGHT_BOUNDS[factor]
    new_weight = round(_clamp(old_weight + delta_weight, lower, upper), 6)
    return {
        "proposal_id": f"wprop_{index:02d}_{factor}",
        "factor": factor,
        "old_weight": old_weight,
        "delta_weight_proposed": delta_weight,
        "new_weight_proposed": new_weight,
        "eta": eta,
        "review_confidence_C": review_confidence,
        "outlier_factor_O": outlier_factor,
        "bias_strength_A": float(finding["bias_strength"]),
        "error_direction_E": direction,
        "delta_points": delta_points,
        "evidence": list(finding.get("evidence") or []),
        "reason": f"{factor} 复盘标签显示存在方向性偏差；仅生成建议，等待管理员审核。",
        "auto_applied": False,
        "admin_review_required": True,
    }


def _rule_proposal(
    prefix: str,
    index: int,
    finding: dict[str, Any],
    *,
    eta: float,
    review_confidence: float,
    outlier_factor: float,
    rule_family: str,
) -> dict[str, Any]:
    direction = int(finding["error_direction"])
    return {
        "proposal_id": f"{prefix}_{index:02d}_{finding['factor']}",
        "rule_family": rule_family,
        "factor": finding["factor"],
        "proposal_target": finding["proposal_target"],
        "delta_points": _delta_points(
            eta=eta,
            review_confidence=review_confidence,
            outlier_factor=outlier_factor,
            bias_strength=float(finding["bias_strength"]),
            error_direction=direction,
            limit=3.0,
        ),
        "eta": eta,
        "review_confidence_C": review_confidence,
        "outlier_factor_O": outlier_factor,
        "bias_strength_A": float(finding["bias_strength"]),
        "error_direction_E": direction,
        "evidence": list(finding.get("evidence") or []),
        "reason": "P1 仅沉淀规则 proposal，不自动修改 live signal 或爆冷规则代码。",
        "auto_applied": False,
        "admin_review_required": True,
    }


def _normalized_weights_preview(config: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, float]:
    preview = {factor: float(value) for factor, value in config["pre_match_weights"].items()}
    for proposal in proposals:
        factor = str(proposal["factor"])
        lower, upper = WEIGHT_BOUNDS[factor]
        preview[factor] = _clamp(preview[factor] + float(proposal["delta_weight_proposed"]), lower, upper)

    total = sum(preview.values())
    normalized = {factor: value / total for factor, value in preview.items()}
    rounded = {factor: round(value, 6) for factor, value in normalized.items()}
    diff = round(1.0 - sum(rounded.values()), 6)
    if diff:
        largest = max(rounded, key=rounded.get)
        rounded[largest] = round(rounded[largest] + diff, 6)
    return rounded


def _review_confidence(payload: dict[str, Any]) -> float:
    explicit = payload.get("review_confidence")
    if explicit is not None:
        return round(_clamp(_number(explicit), 0.2, 1.0), 4)
    flags = [str(item) for item in payload.get("abnormality_flags") or []] or ["normal"]
    return round(min(_confidence_for_flag(flag) for flag in flags), 4)


def _outlier_factor(payload: dict[str, Any]) -> float:
    flags = [str(item) for item in payload.get("abnormality_flags") or []] or ["normal"]
    return round(min(_outlier_for_flag(flag) for flag in flags), 4)


def _confidence_for_flag(flag: str) -> float:
    if flag in CONFIDENCE_BY_ABNORMALITY:
        return CONFIDENCE_BY_ABNORMALITY[flag]
    return UNKNOWN_ABNORMALITY_CONFIDENCE


def _outlier_for_flag(flag: str) -> float:
    if flag in OUTLIER_BY_ABNORMALITY:
        return OUTLIER_BY_ABNORMALITY[flag]
    return UNKNOWN_ABNORMALITY_OUTLIER


def _prediction_error_level(
    *,
    outcome_hit: bool,
    upset_missed: bool,
    favorite_probability: float,
    prediction_confidence: float,
    tags: list[dict[str, Any]],
) -> str:
    if upset_missed:
        return "upset_missed"
    if outcome_hit:
        return "minor"
    severe = favorite_probability >= 0.55
    has_explicit_strong_tag = any(float(tag.get("bias_strength") or 0) == 1.2 for tag in tags)
    if severe and (prediction_confidence >= 55 or has_explicit_strong_tag):
        return "severe"
    return "medium"


def _primary_error_source(payload: dict[str, Any]) -> str:
    tags = list(payload.get("review_tags") or [])
    if not tags:
        return "needs_human_factor_tagging"
    if any(str(tag.get("source_layer")) == "pre_match" and str(tag.get("factor")) in PRE_MATCH_FACTORS for tag in tags):
        return "pre_match_factor_bias"
    if any(str(tag.get("source_layer")) == "live_signal" for tag in tags):
        return "live_signal_rule_gap"
    if any("upset" in str(tag.get("source_layer")) for tag in tags):
        return "upset_rule_gap"
    return "post_match_factor_review"


def _evaluation_reason_codes(payload: dict[str, Any], outcome_hit: bool, over_2_5_hit: bool, upset_missed: bool) -> list[str]:
    reason_codes: list[str] = []
    if not outcome_hit:
        reason_codes.append("outcome_prediction_missed")
    if not over_2_5_hit:
        reason_codes.append("total_goals_prediction_missed")
    if upset_missed:
        reason_codes.append("upset_missed")
    if not payload.get("review_tags"):
        reason_codes.append("needs_human_factor_tagging")
    for flag in payload.get("abnormality_flags") or []:
        reason_codes.append(f"abnormality:{flag}")
    return reason_codes or ["minor_result_variance"]


def _proposal_target(source_layer: str, factor: str) -> str:
    if source_layer == "pre_match" and factor in PRE_MATCH_FACTORS:
        return "pre_match_weight"
    if source_layer == "live_signal":
        return "live_signal_rule"
    if source_layer in {"upset", "upset_rule"} or "upset" in factor or "counter_attack" in factor:
        return "upset_rule"
    if factor in {"xg_quality", "core_player_touch", "tactical_shift"}:
        return "live_signal_rule"
    return "parking_lot"


def _predicted_outcome(probabilities: dict[str, Any]) -> str:
    values = {outcome: _number(probabilities.get(outcome)) for outcome in OUTCOMES}
    return max(values, key=values.get)


def _favorite_team_outcome(probabilities: dict[str, Any]) -> tuple[str, float]:
    home = _number(probabilities.get("home_win"))
    away = _number(probabilities.get("away_win"))
    if home >= away:
        return "home_win", home
    return "away_win", away


def _delta_points(
    *,
    eta: float,
    review_confidence: float,
    outlier_factor: float,
    bias_strength: float,
    error_direction: int,
    limit: float,
) -> float:
    value = eta * review_confidence * outlier_factor * bias_strength * error_direction * 10
    return round(_clamp(value, -limit, limit), 4)


def _severity(bias_strength: float) -> str:
    if bias_strength >= 1.0:
        return "high"
    if bias_strength >= 0.6:
        return "medium"
    return "low"


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
