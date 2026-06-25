from __future__ import annotations

import math
from typing import Any

SCORELINE_MODEL_VERSION = "scoreline-poisson-v1"
DEFAULT_TAIL_TOLERANCE = 1e-8
DEFAULT_INITIAL_MAX_GOAL = 8
DEFAULT_HARD_MAX_GOAL = 15
PROBABILITY_TOLERANCE = 1e-6


class ScorelineModelError(ValueError):
    """Raised when a scoreline distribution cannot satisfy its probability contract."""


def build_scoreline_distribution(
    home_expected_goals: float,
    away_expected_goals: float,
    *,
    rho: float = 0.0,
    tail_tolerance: float = DEFAULT_TAIL_TOLERANCE,
    initial_max_goal: int = DEFAULT_INITIAL_MAX_GOAL,
    hard_max_goal: int = DEFAULT_HARD_MAX_GOAL,
) -> dict[str, Any]:
    _validate_inputs(
        home_expected_goals,
        away_expected_goals,
        rho=rho,
        tail_tolerance=tail_tolerance,
        initial_max_goal=initial_max_goal,
        hard_max_goal=hard_max_goal,
    )
    matrix_max_goal = initial_max_goal
    home_mass: list[float] = []
    away_mass: list[float] = []
    tail_probability = 1.0
    while matrix_max_goal <= hard_max_goal:
        home_mass = _poisson_probabilities(home_expected_goals, matrix_max_goal)
        away_mass = _poisson_probabilities(away_expected_goals, matrix_max_goal)
        tail_probability = max(0.0, 1.0 - sum(home_mass) * sum(away_mass))
        if tail_probability <= tail_tolerance:
            break
        matrix_max_goal += 1
    if tail_probability > tail_tolerance:
        raise ScorelineModelError(
            "scoreline tail probability exceeds tolerance at hard cap: "
            f"tail={tail_probability:.12g}, hard_max_goal={hard_max_goal}"
        )

    raw_cells: list[dict[str, Any]] = []
    raw_captured_probability = 0.0
    family = "independent_poisson" if rho == 0.0 else "dixon_coles"
    for home_goals, home_probability in enumerate(home_mass):
        for away_goals, away_probability in enumerate(away_mass):
            raw_probability = home_probability * away_probability
            correction = _dixon_coles_tau(
                home_goals,
                away_goals,
                home_expected_goals,
                away_expected_goals,
                rho,
            )
            corrected_probability = raw_probability * correction
            if corrected_probability < 0:
                raise ScorelineModelError(
                    "Dixon-Coles correction produced negative probability: "
                    f"score={home_goals}:{away_goals}, rho={rho}"
                )
            raw_captured_probability += corrected_probability
            raw_cells.append(
                {
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "scoreline": f"{home_goals}:{away_goals}",
                    "raw_probability": corrected_probability,
                }
            )
    if raw_captured_probability <= 0:
        raise ScorelineModelError("scoreline matrix has no positive probability mass")

    normalization_factor = 1.0 / raw_captured_probability
    normalized_cells = [
        {
            "home_goals": int(cell["home_goals"]),
            "away_goals": int(cell["away_goals"]),
            "scoreline": str(cell["scoreline"]),
            "probability": float(cell["raw_probability"]) * normalization_factor,
        }
        for cell in raw_cells
    ]
    ranked_cells = _rank_full_distribution(normalized_cells)
    probabilities = aggregate_scoreline_probabilities(ranked_cells)
    result = {
        "version": SCORELINE_MODEL_VERSION,
        "family": family,
        "rho": float(rho),
        "home_expected_goals": float(home_expected_goals),
        "away_expected_goals": float(away_expected_goals),
        "matrix_max_goal": matrix_max_goal,
        "tail_probability": float(tail_probability),
        "raw_captured_probability": float(raw_captured_probability),
        "normalization_factor": float(normalization_factor),
        "probabilities": probabilities,
        "scoreline_distribution": ranked_cells,
        "recommended_scores": [
            str(cell["scoreline"]) for cell in ranked_cells[:3]
        ],
    }
    validate_scoreline_model_result(result)
    return result


def aggregate_scoreline_probabilities(
    matrix: list[dict[str, Any]],
) -> dict[str, float | str]:
    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    over_2_5 = 0.0
    btts_yes = 0.0
    for cell in matrix:
        home_goals = int(cell["home_goals"])
        away_goals = int(cell["away_goals"])
        probability = float(cell["probability"])
        if home_goals > away_goals:
            home_win += probability
        elif home_goals == away_goals:
            draw += probability
        else:
            away_win += probability
        if home_goals + away_goals >= 3:
            over_2_5 += probability
        if home_goals >= 1 and away_goals >= 1:
            btts_yes += probability

    home_win, draw, away_win = _rounded_partition(home_win, draw, away_win)
    over_2_5, under_2_5 = _rounded_complement(over_2_5)
    btts_yes, btts_no = _rounded_complement(btts_yes)
    if home_win >= away_win:
        favorite_side = "home"
        upset_risk = away_win
    else:
        favorite_side = "away"
        upset_risk = home_win
    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "over_2_5": over_2_5,
        "under_2_5": under_2_5,
        "btts_yes": btts_yes,
        "btts_no": btts_no,
        "upset_risk": round(upset_risk, 6),
        "favorite_side": favorite_side,
    }


def _rank_full_distribution(matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = select_top_scorelines(matrix, limit=len(matrix))
    rounded_total = sum(float(cell["probability"]) for cell in ranked)
    delta = round(1.0 - rounded_total, 6)
    if ranked and delta:
        ranked[0]["probability"] = round(float(ranked[0]["probability"]) + delta, 6)
        ranked = select_top_scorelines(ranked, limit=len(ranked))
    return ranked


def select_top_scorelines(
    matrix: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    ordered = sorted(
        matrix,
        key=lambda cell: (
            -float(cell["probability"]),
            int(cell["home_goals"]) + int(cell["away_goals"]),
            abs(int(cell["home_goals"]) - int(cell["away_goals"])),
            -int(cell["home_goals"]),
            int(cell["away_goals"]),
        ),
    )
    return [
        {
            "home_goals": int(cell["home_goals"]),
            "away_goals": int(cell["away_goals"]),
            "scoreline": str(cell["scoreline"]),
            "probability": round(float(cell["probability"]), 6),
            "rank": rank,
        }
        for rank, cell in enumerate(ordered[:limit], start=1)
    ]


def validate_scoreline_model_result(result: dict[str, Any]) -> None:
    required = {
        "version",
        "family",
        "rho",
        "home_expected_goals",
        "away_expected_goals",
        "matrix_max_goal",
        "tail_probability",
        "raw_captured_probability",
        "normalization_factor",
        "probabilities",
        "scoreline_distribution",
        "recommended_scores",
    }
    missing = sorted(required - set(result))
    if missing:
        raise ScorelineModelError(
            f"scoreline model result missing keys: {', '.join(missing)}"
        )
    if result["version"] != SCORELINE_MODEL_VERSION:
        raise ScorelineModelError("unexpected scoreline model version")
    family = str(result["family"])
    rho = float(result["rho"])
    if family not in {"independent_poisson", "dixon_coles"}:
        raise ScorelineModelError(f"unsupported scoreline family:{family}")
    if (rho == 0.0 and family != "independent_poisson") or (
        rho != 0.0 and family != "dixon_coles"
    ):
        raise ScorelineModelError("scoreline family and rho are inconsistent")
    matrix_max_goal = int(result["matrix_max_goal"])
    distribution = result["scoreline_distribution"]
    if not isinstance(distribution, list) or not distribution:
        raise ScorelineModelError("scoreline distribution must be non-empty")
    expected_cell_count = (matrix_max_goal + 1) ** 2
    if len(distribution) != expected_cell_count:
        raise ScorelineModelError(
            "scoreline distribution cell count does not match matrix cap"
        )
    seen_scorelines: set[str] = set()
    previous_probability = math.inf
    for expected_rank, cell in enumerate(distribution, start=1):
        required_cell_keys = {
            "home_goals",
            "away_goals",
            "scoreline",
            "probability",
            "rank",
        }
        if not required_cell_keys.issubset(cell):
            raise ScorelineModelError("scoreline cell contract is incomplete")
        home_goals = int(cell["home_goals"])
        away_goals = int(cell["away_goals"])
        scoreline = str(cell["scoreline"])
        probability = float(cell["probability"])
        if not 0 <= home_goals <= matrix_max_goal or not 0 <= away_goals <= matrix_max_goal:
            raise ScorelineModelError("scoreline cell exceeds matrix cap")
        if scoreline != f"{home_goals}:{away_goals}":
            raise ScorelineModelError("scoreline label does not match goal coordinates")
        if scoreline in seen_scorelines:
            raise ScorelineModelError(f"duplicate scoreline cell:{scoreline}")
        seen_scorelines.add(scoreline)
        if int(cell["rank"]) != expected_rank:
            raise ScorelineModelError("scoreline ranks must be contiguous")
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ScorelineModelError("scoreline cell probability must be finite in 0..1")
        if probability > previous_probability + PROBABILITY_TOLERANCE:
            raise ScorelineModelError("scoreline distribution must be probability-ranked")
        previous_probability = probability
    total = sum(float(cell["probability"]) for cell in distribution)
    if abs(total - 1.0) > PROBABILITY_TOLERANCE:
        raise ScorelineModelError(
            f"scoreline distribution must sum to one, got {total:.9f}"
        )
    probabilities = result["probabilities"]
    recomputed = aggregate_scoreline_probabilities(distribution)
    for key in (
        "home_win",
        "draw",
        "away_win",
        "over_2_5",
        "under_2_5",
        "btts_yes",
        "btts_no",
        "upset_risk",
    ):
        if abs(float(probabilities[key]) - float(recomputed[key])) > 2e-6:
            raise ScorelineModelError(
                f"matrix aggregate drifted for {key}: "
                f"exposed={probabilities[key]}, recomputed={recomputed[key]}"
            )
    if str(probabilities["favorite_side"]) != str(recomputed["favorite_side"]):
        raise ScorelineModelError("matrix favorite_side drifted")
    _validate_partition(
        probabilities,
        ("home_win", "draw", "away_win"),
        "1x2",
    )
    _validate_partition(
        probabilities,
        ("over_2_5", "under_2_5"),
        "over_under",
    )
    _validate_partition(
        probabilities,
        ("btts_yes", "btts_no"),
        "btts",
    )
    recommended = [str(value) for value in result["recommended_scores"]]
    expected = [str(cell["scoreline"]) for cell in distribution[:3]]
    if recommended != expected:
        raise ScorelineModelError("recommended scores must equal top three scorelines")


def _poisson_probabilities(expected_goals: float, max_goal: int) -> list[float]:
    probabilities = [math.exp(-expected_goals)]
    for goals in range(1, max_goal + 1):
        probabilities.append(probabilities[-1] * expected_goals / goals)
    return probabilities


def _dixon_coles_tau(
    home_goals: int,
    away_goals: int,
    home_expected_goals: float,
    away_expected_goals: float,
    rho: float,
) -> float:
    if rho == 0.0:
        return 1.0
    if home_goals == 0 and away_goals == 0:
        return 1.0 - home_expected_goals * away_expected_goals * rho
    if home_goals == 0 and away_goals == 1:
        return 1.0 + home_expected_goals * rho
    if home_goals == 1 and away_goals == 0:
        return 1.0 + away_expected_goals * rho
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


def _rounded_partition(*values: float) -> tuple[float, ...]:
    total = sum(values)
    if total <= 0:
        raise ScorelineModelError("probability partition has no mass")
    normalized = [value / total for value in values]
    rounded = [round(value, 6) for value in normalized[:-1]]
    rounded.append(round(1.0 - sum(rounded), 6))
    return tuple(rounded)


def _rounded_complement(value: float) -> tuple[float, float]:
    bounded = min(1.0, max(0.0, value))
    rounded = round(bounded, 6)
    return rounded, round(1.0 - rounded, 6)


def _validate_partition(
    probabilities: dict[str, Any],
    keys: tuple[str, ...],
    label: str,
) -> None:
    values = [float(probabilities[key]) for key in keys]
    if any(value < 0 or value > 1 for value in values):
        raise ScorelineModelError(f"{label} probabilities must be in 0..1")
    total = sum(values)
    if abs(total - 1.0) > 2e-6:
        raise ScorelineModelError(
            f"{label} probabilities must sum to one, got {total:.9f}"
        )


def _validate_inputs(
    home_expected_goals: float,
    away_expected_goals: float,
    *,
    rho: float,
    tail_tolerance: float,
    initial_max_goal: int,
    hard_max_goal: int,
) -> None:
    for label, value in (
        ("home_expected_goals", home_expected_goals),
        ("away_expected_goals", away_expected_goals),
        ("rho", rho),
        ("tail_tolerance", tail_tolerance),
    ):
        if not math.isfinite(value):
            raise ScorelineModelError(f"{label} must be finite")
    if home_expected_goals < 0 or away_expected_goals < 0:
        raise ScorelineModelError("expected goals must be non-negative")
    if not 0 < tail_tolerance < 1:
        raise ScorelineModelError("tail_tolerance must be in 0..1")
    if initial_max_goal < 0:
        raise ScorelineModelError("initial_max_goal must be non-negative")
    if hard_max_goal < initial_max_goal:
        raise ScorelineModelError("hard_max_goal must be >= initial_max_goal")
