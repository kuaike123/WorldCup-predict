from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import load_weights
from .market_signal_score import calculate_market_signal
from .pre_match_score import calculate_dimension_scores
from .risk_engine import calculate_probabilities, directions, evaluate_risk, weighted_scores
from .schema_validator import validate_analysis_output, validate_match_input


DISCLAIMER = "以上为赛事数据分析参考，不构成收益承诺。"


def analyze_match(match: dict[str, Any], weights: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_match_input(match)
    config = weights or load_weights()
    market_signal = calculate_market_signal(match, config)
    breakdown = calculate_dimension_scores(match, market_signal)
    home_score, away_score = weighted_scores(breakdown, config)
    score_gap = round(home_score - away_score, 2)
    probabilities = calculate_probabilities(home_score, away_score, breakdown)
    risk_level, confidence, reason_codes, missing_fields, data_quality = evaluate_risk(
        score_gap,
        breakdown,
        market_signal,
        probabilities,
        match,
        config,
    )
    main_direction, secondary_direction, recommended_scores = directions(
        match["home_team"],
        match["away_team"],
        score_gap,
        probabilities,
    )
    explanation = (
        f"{match['home_team']} 综合分 {home_score}，{match['away_team']} 综合分 {away_score}，"
        f"分差 {score_gap}。市场信号为 {market_signal['direction']}，风险等级 {risk_level}。"
    )
    output = {
        "match_id": match["match_id"],
        "weights_version": config["version"],
        "home_team": match["home_team"],
        "away_team": match["away_team"],
        "home_score": home_score,
        "away_score": away_score,
        "score_gap": score_gap,
        "probabilities": probabilities,
        "market_signal": market_signal,
        "risk_level": risk_level,
        "confidence": confidence,
        "main_direction": main_direction,
        "secondary_direction": secondary_direction,
        "recommended_scores": recommended_scores,
        "dimension_breakdown": breakdown,
        "reason_codes": reason_codes,
        "explanation": explanation,
        "disclaimer": DISCLAIMER,
        "data_quality_score": data_quality,
        "missing_fields": missing_fields,
        "input_snapshot": deepcopy(match),
    }
    validate_analysis_output(output)
    return output
