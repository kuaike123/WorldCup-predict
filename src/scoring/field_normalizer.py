from __future__ import annotations

from typing import Any


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def bool_score(value: Any, true_score: float, false_score: float) -> float | None:
    if value is None:
        return None
    return true_score if bool(value) else false_score


def ratio_to_score(value: Any) -> float | None:
    if value is None:
        return None
    return clamp(float(value) * 100)


def ppg_to_score(value: Any) -> float | None:
    if value is None:
        return None
    return clamp(float(value) / 3.0 * 100)


def goal_diff_to_score(value: Any) -> float | None:
    if value is None:
        return None
    return clamp(50 + float(value) * 6)


def positive_rate_score(value: Any, scale: float, base: float = 0.0) -> float | None:
    if value is None:
        return None
    return clamp(base + float(value) * scale)


def inverse_rate_score(value: Any, scale: float, base: float = 100.0) -> float | None:
    if value is None:
        return None
    return clamp(base - float(value) * scale)


def average_available(values: list[float | None]) -> tuple[float, float]:
    available = [value for value in values if value is not None]
    if not available:
        return 50.0, 0.0
    return round(sum(available) / len(available), 2), round(len(available) / len(values), 3)
