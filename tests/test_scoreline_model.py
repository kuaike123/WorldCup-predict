from __future__ import annotations

import math

import pytest

from src.scoring.scoreline_model import (
    ScorelineModelError,
    aggregate_scoreline_probabilities,
    build_scoreline_distribution,
    select_top_scorelines,
)


def _cell(result: dict, scoreline: str) -> dict:
    return next(
        item for item in result["scoreline_distribution"] if item["scoreline"] == scoreline
    )


def test_scoreline_distribution_sums_to_one_and_derives_consistent_markets() -> None:
    result = build_scoreline_distribution(1.8, 1.2)

    assert sum(item["probability"] for item in result["scoreline_distribution"]) == pytest.approx(
        1.0,
        abs=1e-6,
    )
    probabilities = result["probabilities"]
    assert probabilities["home_win"] + probabilities["draw"] + probabilities["away_win"] == pytest.approx(1.0)
    assert probabilities["over_2_5"] + probabilities["under_2_5"] == pytest.approx(1.0)
    assert probabilities["btts_yes"] + probabilities["btts_no"] == pytest.approx(1.0)
    assert result["recommended_scores"] == [
        item["scoreline"] for item in result["scoreline_distribution"][:3]
    ]
    assert result["tail_probability"] <= 1e-8


def test_symmetric_lambdas_produce_symmetric_home_and_away_probabilities() -> None:
    result = build_scoreline_distribution(1.3, 1.3)

    assert result["probabilities"]["home_win"] == pytest.approx(
        result["probabilities"]["away_win"],
        abs=1e-6,
    )


def test_increasing_home_lambda_increases_home_win_probability() -> None:
    lower = build_scoreline_distribution(1.0, 1.1)
    higher = build_scoreline_distribution(2.0, 1.1)

    assert higher["probabilities"]["home_win"] > lower["probabilities"]["home_win"]


def test_increasing_both_lambdas_increases_over_probability() -> None:
    lower = build_scoreline_distribution(0.8, 0.7)
    higher = build_scoreline_distribution(1.8, 1.7)

    assert higher["probabilities"]["over_2_5"] > lower["probabilities"]["over_2_5"]


def test_rho_zero_is_identical_to_independent_poisson() -> None:
    implicit = build_scoreline_distribution(1.4, 0.9)
    explicit = build_scoreline_distribution(1.4, 0.9, rho=0.0)

    assert implicit == explicit
    assert implicit["family"] == "independent_poisson"


def test_dixon_coles_changes_only_four_raw_cells_before_normalization() -> None:
    independent = build_scoreline_distribution(1.4, 0.9, rho=0.0)
    corrected = build_scoreline_distribution(1.4, 0.9, rho=-0.1)
    independent_factor = independent["normalization_factor"]
    corrected_factor = corrected["normalization_factor"]

    changed = []
    for item in independent["scoreline_distribution"]:
        scoreline = item["scoreline"]
        left = item["probability"] / independent_factor
        right = _cell(corrected, scoreline)["probability"] / corrected_factor
        if not math.isclose(left, right, rel_tol=0.0, abs_tol=2e-6):
            changed.append(scoreline)

    assert set(changed) == {"0:0", "0:1", "1:0", "1:1"}
    assert corrected["family"] == "dixon_coles"


def test_invalid_rho_that_creates_negative_cell_is_rejected() -> None:
    with pytest.raises(ScorelineModelError, match="negative probability"):
        build_scoreline_distribution(1.0, 1.0, rho=-2.0)


def test_adaptive_cap_expands_for_high_expected_goals() -> None:
    result = build_scoreline_distribution(2.3, 2.0, initial_max_goal=4)

    assert result["matrix_max_goal"] > 4
    assert result["tail_probability"] <= 1e-8


def test_top_scoreline_order_is_deterministic() -> None:
    matrix = [
        {"home_goals": 1, "away_goals": 0, "scoreline": "1:0", "probability": 0.2},
        {"home_goals": 0, "away_goals": 1, "scoreline": "0:1", "probability": 0.2},
        {"home_goals": 1, "away_goals": 1, "scoreline": "1:1", "probability": 0.2},
        {"home_goals": 0, "away_goals": 0, "scoreline": "0:0", "probability": 0.2},
        {"home_goals": 2, "away_goals": 0, "scoreline": "2:0", "probability": 0.2},
    ]

    ordered = select_top_scorelines(matrix, limit=5)

    assert [item["scoreline"] for item in ordered] == ["0:0", "1:0", "0:1", "1:1", "2:0"]


def test_aggregate_scoreline_probabilities_matches_manual_matrix() -> None:
    matrix = [
        {"home_goals": 1, "away_goals": 0, "probability": 0.4},
        {"home_goals": 1, "away_goals": 1, "probability": 0.3},
        {"home_goals": 0, "away_goals": 1, "probability": 0.2},
        {"home_goals": 2, "away_goals": 1, "probability": 0.1},
    ]

    probabilities = aggregate_scoreline_probabilities(matrix)

    assert probabilities["home_win"] == 0.5
    assert probabilities["draw"] == 0.3
    assert probabilities["away_win"] == 0.2
    assert probabilities["over_2_5"] == 0.1
    assert probabilities["btts_yes"] == 0.4


def test_tail_contract_rejects_impossible_hard_cap() -> None:
    with pytest.raises(ScorelineModelError, match="tail probability"):
        build_scoreline_distribution(
            5.0,
            5.0,
            initial_max_goal=2,
            hard_max_goal=2,
        )


def test_validator_rejects_probability_not_derived_from_serialized_matrix() -> None:
    result = build_scoreline_distribution(1.5, 1.0)
    result["probabilities"]["home_win"] = round(
        result["probabilities"]["home_win"] + 0.01,
        6,
    )
    result["probabilities"]["away_win"] = round(
        result["probabilities"]["away_win"] - 0.01,
        6,
    )

    from src.scoring.scoreline_model import validate_scoreline_model_result

    with pytest.raises(ScorelineModelError, match="matrix aggregate drifted"):
        validate_scoreline_model_result(result)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda result: result["scoreline_distribution"].__setitem__(1, result["scoreline_distribution"][0].copy()), "duplicate scoreline"),
        (lambda result: result["scoreline_distribution"][0].__setitem__("rank", 2), "ranks must be contiguous"),
        (lambda result: result["scoreline_distribution"][0].__setitem__("scoreline", "9:9"), "label does not match"),
    ],
)
def test_validator_rejects_malformed_scoreline_distribution(
    mutation,
    message: str,
) -> None:
    from src.scoring.scoreline_model import validate_scoreline_model_result

    result = build_scoreline_distribution(1.5, 1.0)
    mutation(result)

    with pytest.raises(ScorelineModelError, match=message):
        validate_scoreline_model_result(result)
