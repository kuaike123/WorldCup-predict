from __future__ import annotations

import math
from typing import Any

from .field_normalizer import clamp


def weighted_scores(breakdown: list[dict[str, Any]], weights: dict[str, Any]) -> tuple[float, float]:
    weight_map = weights["pre_match_weights"]
    home_total = 0.0
    away_total = 0.0
    for item in breakdown:
        weight = float(weight_map[item["dimension"]])
        home_total += float(item["home_score"]) * weight
        away_total += float(item["away_score"]) * weight
    return round(home_total, 2), round(away_total, 2)


def calculate_probabilities(home_score: float, away_score: float, breakdown: list[dict[str, Any]]) -> dict[str, float]:
    gap = home_score - away_score
    home_raw = 1 / (1 + math.exp(-gap / 11))
    draw = max(0.18, min(0.32, 0.30 - abs(gap) * 0.006))
    home_win = home_raw * (1 - draw)
    away_win = (1 - home_raw) * (1 - draw)
    attack = next(item for item in breakdown if item["dimension"] == "attack_defense_efficiency")
    attack_avg = (attack["home_score"] + attack["away_score"]) / 2
    over_2_5 = clamp(0.36 + (attack_avg - 50) / 120, 0.25, 0.72)
    upset_risk = clamp(0.12 + max(0, 12 - abs(gap)) / 45, 0.08, 0.42)
    return {
        "home_win": round(home_win, 3),
        "draw": round(draw, 3),
        "away_win": round(away_win, 3),
        "over_2_5": round(over_2_5, 3),
        "upset_risk": round(upset_risk, 3),
    }


def evaluate_risk(
    score_gap: float,
    breakdown: list[dict[str, Any]],
    market_signal: dict[str, Any],
    probabilities: dict[str, float],
    match: dict[str, Any],
    weights: dict[str, Any],
) -> tuple[str, float, list[str], list[str], float]:
    reason_codes: list[str] = []
    risk_points = 0
    if abs(score_gap) < weights["risk_thresholds"]["close_score_gap"]:
        risk_points += 1
        reason_codes.append("close_score_gap")
    market_direction = market_signal["direction"]
    if (score_gap > 4 and market_direction.startswith("away")) or (score_gap < -4 and market_direction.startswith("home")):
        risk_points += 1
        reason_codes.append("market_fundamental_conflict")
    if "home_heat_risk" in market_signal["risk_flags"] or "over_heat_risk" in market_signal["risk_flags"]:
        risk_points += 1
        reason_codes.append("market_heat_risk")

    all_missing = [field for item in breakdown for field in item["missing_fields"]]
    expected_missing_slots = 44
    missing_ratio = len(all_missing) / expected_missing_slots
    if missing_ratio > weights["risk_thresholds"]["missing_field_ratio_high"]:
        risk_points += 1
        reason_codes.append("high_missing_field_ratio")
    if "high_odds_dispersion" in market_signal["risk_flags"]:
        risk_points += 1
        reason_codes.append("high_odds_dispersion")
    knockout = bool(match["team_features"]["home"].get("knockout") or match["team_features"]["away"].get("knockout"))
    if knockout:
        risk_points = max(risk_points, 1)
        reason_codes.append("knockout_or_life_death_stage")

    risk_level = "low" if risk_points == 0 else "medium" if risk_points <= 2 else "high"
    if probabilities["upset_risk"] > 0.32:
        risk_level = "high"
        reason_codes.append("upset_risk_high")

    quality_scores = [float(item["quality_score"]) for item in breakdown]
    data_quality = round(sum(quality_scores) / len(quality_scores), 3)
    confidence = 72 + abs(score_gap) * 0.9 - risk_points * 9 - (1 - data_quality) * 26
    confidence = round(clamp(confidence, 0, 100), 2)
    if confidence < weights["risk_thresholds"]["low_confidence_floor"]:
        risk_level = "high"
        reason_codes.append("low_confidence")
    return risk_level, confidence, sorted(set(reason_codes)), sorted(set(all_missing)), data_quality


def directions(home_team: str, away_team: str, score_gap: float, probabilities: dict[str, float]) -> tuple[str, str, list[str]]:
    if score_gap >= 8:
        main = f"{home_team} 不败方向更强"
        scores = ["2:1", "1:0", "1:1"]
    elif score_gap <= -8:
        main = f"{away_team} 不败方向更强"
        scores = ["1:2", "0:1", "1:1"]
    elif score_gap >= 2:
        main = f"{home_team} 小幅占优，防平"
        scores = ["1:1", "2:1", "1:0"]
    elif score_gap <= -2:
        main = f"{away_team} 小幅占优，防平"
        scores = ["1:1", "1:2", "0:1"]
    else:
        main = "双方接近，优先防平局"
        scores = ["1:1", "0:0", "2:1"]
    secondary = "大 2.5 倾向可关注" if probabilities["over_2_5"] >= 0.53 else "大 2.5 优势不明显，谨慎观察"
    return main, secondary, scores
