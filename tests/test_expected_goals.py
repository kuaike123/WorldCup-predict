from __future__ import annotations

import pytest

from src.scoring.expected_goals import (
    ExpectedGoalsParams,
    infer_expected_goals_from_team_features,
)


def _team(
    *,
    goals_for: float = 1.5,
    goals_against: float = 1.0,
    prior: float = 1.25,
) -> dict[str, float]:
    return {
        "shrunk_goals_for": goals_for,
        "shrunk_goals_against": goals_against,
        "prior_goals_per_team": prior,
    }


def test_neutral_field_disables_home_advantage() -> None:
    neutral = infer_expected_goals_from_team_features(
        _team(),
        _team(),
        neutral_field=True,
        params=ExpectedGoalsParams(home_advantage_factor=1.2),
    )
    non_neutral = infer_expected_goals_from_team_features(
        _team(),
        _team(),
        neutral_field=False,
        params=ExpectedGoalsParams(home_advantage_factor=1.2),
    )

    assert neutral["home_advantage_factor"] == 1.0
    assert non_neutral["home_advantage_factor"] == 1.2
    assert non_neutral["home_expected_goals"] == pytest.approx(
        neutral["home_expected_goals"] * 1.2,
        abs=1e-6,
    )
    assert non_neutral["away_expected_goals"] == neutral["away_expected_goals"]


def test_missing_rates_fall_back_to_auditable_prior() -> None:
    result = infer_expected_goals_from_team_features(
        {"prior_goals_per_team": 1.3},
        {"prior_goals_per_team": 1.3},
        neutral_field=True,
    )

    assert result["prior_goals_per_team"] == 1.3
    assert result["home_expected_goals"] == 1.3
    assert result["away_expected_goals"] == 1.3
    assert result["lambda_clamps"] == []


def test_optional_adjustments_are_quality_weighted_and_bounded() -> None:
    components = [
        {
            "dimension": dimension,
            "home_value": 100.0,
            "away_value": 0.0,
            "quality_score": 1.0,
        }
        for dimension in (
            "lineup_integrity",
            "key_player_status",
            "schedule_fatigue",
            "motivation_stage",
        )
    ]
    result = infer_expected_goals_from_team_features(
        _team(),
        _team(),
        neutral_field=True,
        components=components,
        params=ExpectedGoalsParams(optional_adjustments_enabled=True),
    )

    assert result["home_optional_adjustment_factor"] == 1.2
    assert result["away_optional_adjustment_factor"] == 0.8
    assert len(result["adjustment_details"]) == 4


def test_unavailable_optional_components_are_neutral() -> None:
    result = infer_expected_goals_from_team_features(
        _team(),
        _team(),
        neutral_field=True,
        components=[
            {
                "dimension": "lineup_integrity",
                "home_value": 90.0,
                "away_value": 10.0,
                "quality_score": 0.0,
            }
        ],
        params=ExpectedGoalsParams(optional_adjustments_enabled=True),
    )

    assert result["home_optional_adjustment_factor"] == 1.0
    assert result["away_optional_adjustment_factor"] == 1.0


def test_extreme_rates_are_clamped_and_audited() -> None:
    result = infer_expected_goals_from_team_features(
        _team(goals_for=10.0, goals_against=0.1),
        _team(goals_for=0.1, goals_against=10.0),
        neutral_field=False,
        params=ExpectedGoalsParams(home_advantage_factor=1.2),
    )

    assert result["home_expected_goals"] == 5.0
    assert result["away_expected_goals"] == 0.05
    assert {item["side"] for item in result["lambda_clamps"]} == {"home", "away"}
