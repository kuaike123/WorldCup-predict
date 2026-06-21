from __future__ import annotations

from collections import defaultdict
from statistics import pstdev
from typing import Any

from .field_normalizer import clamp


def _direction_from_delta(delta: float, positive: str, negative: str) -> str:
    if delta > 2:
        return positive
    if delta < -2:
        return negative
    return "neutral"


def _first_latest_by_bookmaker(snapshots: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snapshot in snapshots:
        groups[str(snapshot["bookmaker"])].append(snapshot)
    pairs = []
    for entries in groups.values():
        ordered = sorted(entries, key=lambda item: item["snapshot_time"])
        if len(ordered) >= 2:
            pairs.append((ordered[0], ordered[-1]))
    return pairs


def calculate_market_signal(match: dict[str, Any], weights: dict[str, Any]) -> dict[str, Any]:
    snapshots = match.get("odds_snapshots", [])
    internal = weights["market_signal_internal_weights"]
    pairs = _first_latest_by_bookmaker(snapshots)
    if not pairs:
        return {
            "score": 50,
            "direction": "neutral_low_quality",
            "strength": "low",
            "risk_flags": ["odds_data_insufficient"],
            "quality_score": 0.0,
            "components": {},
            "explanation": "盘口快照不足，市场信号仅作为低质量中性参考。"
        }

    euro_scores = []
    asian_scores = []
    total_scores = []
    home_latest_odds = []
    home_supported_count = 0
    away_supported_count = 0
    over_supported_count = 0
    risk_flags: list[str] = []

    for first, latest in pairs:
        if "home_win_odds" in first and "home_win_odds" in latest:
            first_prob = 1 / float(first["home_win_odds"])
            latest_prob = 1 / float(latest["home_win_odds"])
            delta = (latest_prob - first_prob) * 100
            euro_scores.append(clamp(50 + delta * 4))
            if delta > 1:
                home_supported_count += 1
            elif delta < -1:
                away_supported_count += 1
            home_latest_odds.append(float(latest["home_win_odds"]))
        if "asian_handicap" in first and "asian_handicap" in latest:
            line_delta = float(first["asian_handicap"]) - float(latest["asian_handicap"])
            water_delta = float(first.get("home_water", 1.0)) - float(latest.get("home_water", 1.0))
            asian_score = 50 + line_delta * 18 + water_delta * 12
            asian_scores.append(clamp(asian_score))
            if line_delta > 0.1 or water_delta > 0.08:
                home_supported_count += 1
            elif line_delta < -0.1:
                away_supported_count += 1
            if abs(water_delta) > 0.1 and abs(line_delta) <= 0.05:
                risk_flags.append("risk_control_movement")
        if "total_goals_line" in first and "total_goals_line" in latest:
            line_delta = float(latest["total_goals_line"]) - float(first["total_goals_line"])
            over_water_delta = float(first.get("over_water", 1.0)) - float(latest.get("over_water", 1.0))
            total_score = 50 + line_delta * 16 + over_water_delta * 10
            total_scores.append(clamp(total_score))
            if line_delta > 0.1 or over_water_delta > 0.08:
                over_supported_count += 1
            if over_water_delta > 0.08 and abs(line_delta) <= 0.05:
                risk_flags.append("over_heat_risk")

    def avg(values: list[float], fallback: float = 50.0) -> float:
        return sum(values) / len(values) if values else fallback

    consensus_base = 50
    if pairs:
        same_side = max(home_supported_count, away_supported_count)
        consensus_base += min(20, same_side / max(1, len(pairs)) * 20)
    dispersion = pstdev(home_latest_odds) / avg(home_latest_odds) if len(home_latest_odds) > 1 else 0
    if dispersion > weights["risk_thresholds"]["high_odds_dispersion"]:
        risk_flags.append("high_odds_dispersion")
        consensus_base -= 10

    european = avg(euro_scores)
    asian = avg(asian_scores)
    total_goals = avg(total_scores)
    consensus = clamp(consensus_base)
    score = (
        european * internal["european_odds_movement"]
        + asian * internal["asian_handicap_movement"]
        + total_goals * internal["over_under_movement"]
        + consensus * internal["bookmaker_consensus"]
    )
    direction = _direction_from_delta(score - 50, "home_market_positive", "away_market_positive")
    if home_supported_count >= 2 and away_supported_count == 0:
        direction = "home_market_positive"
    elif away_supported_count >= 2 and home_supported_count == 0:
        direction = "away_market_positive"
    if max(home_supported_count, away_supported_count) >= 2 and len(pairs) >= 2:
        risk_flags.append("information_movement")
    if home_supported_count >= 2 and avg(asian_scores) <= 52:
        risk_flags.append("home_heat_risk")

    strength = "high" if abs(score - 50) >= 12 else "medium" if abs(score - 50) >= 5 else "low"
    quality_score = min(1.0, len(pairs) / 3)
    return {
        "score": round(score, 2),
        "direction": direction,
        "strength": strength,
        "risk_flags": sorted(set(risk_flags)),
        "quality_score": round(quality_score, 3),
        "components": {
            "european_odds_movement": round(european, 2),
            "asian_handicap_movement": round(asian, 2),
            "over_under_movement": round(total_goals, 2),
            "bookmaker_consensus": round(consensus, 2),
            "odds_dispersion": round(dispersion, 4)
        },
        "explanation": f"市场信号 {direction}，强度 {strength}，综合分 {score:.1f}。"
    }
