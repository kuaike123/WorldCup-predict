from __future__ import annotations

from src.scoring.bayesian_calibration import build_bayesian_calibration


def test_calibration_preserves_non_calibrated_probability_fields() -> None:
    prediction = {
        "probabilities": {
            "home_win": 0.54,
            "draw": 0.24,
            "away_win": 0.22,
            "over_2_5": 0.46,
            "upset_risk": 0.18,
            "under_2_5": 0.54,
            "btts_yes": 0.49,
            "btts_no": 0.51,
        }
    }
    reviews = [
        {
            "match_id": f"official_review_{index}",
            "actual_result": {
                "actual_outcome": "home_win",
                "over_2_5_result": True,
            },
            "prediction_snapshot": {
                "probabilities": {
                    "home_win": 0.54,
                    "away_win": 0.22,
                }
            },
            "sample_provenance": {
                "sample_scope": "official_world_cup",
                "official_world_cup_match": True,
                "calibration_sample_weight": 1.0,
            },
        }
        for index in range(20)
    ]

    calibration = build_bayesian_calibration(prediction, reviews)

    assert calibration["applied"] is True
    assert calibration["calibrated_probabilities"]["under_2_5"] == 0.54
    assert calibration["calibrated_probabilities"]["btts_yes"] == 0.49
    assert calibration["calibrated_probabilities"]["btts_no"] == 0.51
