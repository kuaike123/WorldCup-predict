from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.research_db import world_cup_prediction_cli as cli
from app.research_db.repository import ResearchDatabaseRepository


def _seed_fixture(path: Path) -> None:
    ResearchDatabaseRepository(path).upsert_facts(
        {
            "teams": [
                {
                    "team_id": "team_home",
                    "canonical_name": "Home FC",
                    "source": "sample",
                    "source_team_id": "home",
                    "available_at": "2026-06-01T00:00:00+00:00",
                },
                {
                    "team_id": "team_away",
                    "canonical_name": "Away FC",
                    "source": "sample",
                    "source_team_id": "away",
                    "available_at": "2026-06-01T00:00:00+00:00",
                },
            ],
            "fixtures": [
                {
                    "fixture_id": "fixture_wc2026_open_1",
                    "competition": "2026 FIFA World Cup",
                    "season": "2026",
                    "home_team_id": "team_home",
                    "away_team_id": "team_away",
                    "match_time": "2026-06-13T00:00:00+00:00",
                    "neutral_field": True,
                    "source": "sample",
                    "source_fixture_id": "fixture-source-1",
                    "available_at": "2026-06-01T00:00:00+00:00",
                }
            ],
        }
    )


def test_prediction_cli_emits_fixed_contract_without_backfill(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "research.db"
    _seed_fixture(db_path)

    class FakeService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_prediction(self, fixture_id: str) -> dict[str, Any]:
            assert fixture_id == "fixture_wc2026_open_1"
            return {
                "fixture_id": fixture_id,
                "home_team": "Home FC",
                "away_team": "Away FC",
                "probabilities": {
                    "home_win": 0.45,
                    "draw": 0.28,
                    "away_win": 0.27,
                    "over_2_5": 0.52,
                    "under_2_5": 0.48,
                    "btts_yes": 0.49,
                    "btts_no": 0.51,
                },
                "risk": {"level": "medium", "confidence": 61},
                "coverage": {"status": "partial"},
                "calibration": {"applied": False},
                "expected_goals": {"home_expected_goals": 1.4, "away_expected_goals": 1.1},
                "scoreline_model": {"family": "independent_poisson"},
                "prediction_routing": {"scoreline": {"status": "available"}},
                "recommended_scores": ["1:1", "1:0", "2:1"],
                "input_summary": {"team_strength_source": "team_strength_snapshots"},
            }

    monkeypatch.setattr(cli, "PreMatchResearchScoringService", FakeService)

    payload = cli.run_world_cup_prediction(
        db_path=db_path,
        output_dir=tmp_path / "bundle",
        fixture_ids=["fixture_wc2026_open_1"],
        run_backfill=False,
    )

    prediction = payload["predictions"][0]
    assert payload["schema_version"] == "world_cup_prediction.v1"
    assert payload["status"] == "partial"
    assert payload["source"] == {"research_provider": "existing_db", "odds_provider": "existing_db"}
    assert prediction["probabilities"]["under_2_5"] == 0.48
    assert prediction["vip"]["main_pick"] == "home_win"
    assert prediction["vip"]["secondary_pick"] == "over_2_5"
    assert prediction["vip"]["scorelines"] == ["1:1", "1:0", "2:1"]
    assert prediction["recommended_scores"] == ["1:1", "1:0", "2:1"]
    assert prediction["prediction_routing"]["scoreline"]["status"] == "available"
    assert prediction["expected_goals"]["home_expected_goals"] == 1.4
    assert "unsupported_probability:btts_yes" not in prediction["gaps"]
    assert "fixture_id" in payload["output_contract"]["required_per_fixture_fields"]


def test_prediction_cli_runs_backfill_before_prediction_by_default(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "research.db"
    _seed_fixture(db_path)
    calls = []

    def fake_backfill(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "ok",
            "data_quality": {
                "recent_results": "ok",
                "player_form": "ok",
                "odds": "ok",
            },
            "source": {
                "research_provider": "sportradar_soccer",
                "odds_provider": "the_odds_api",
            },
        }

    class FakeService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_prediction(self, fixture_id: str) -> dict[str, Any]:
            return {
                "fixture_id": fixture_id,
                "home_team": "Home FC",
                "away_team": "Away FC",
                "probabilities": {
                    "home_win": 0.45,
                    "draw": 0.28,
                    "away_win": 0.27,
                    "over_2_5": 0.52,
                    "under_2_5": 0.48,
                    "btts_yes": 0.49,
                    "btts_no": 0.51,
                },
                "risk": {"level": "medium", "confidence": 61},
                "coverage": {"status": "ok"},
                "calibration": {"applied": False},
                "expected_goals": {"home_expected_goals": 1.4, "away_expected_goals": 1.1},
                "scoreline_model": {"family": "independent_poisson"},
                "prediction_routing": {"scoreline": {"status": "available"}},
                "recommended_scores": ["1:1"],
            }

    monkeypatch.setattr(cli, "run_targeted_backfill", fake_backfill)
    monkeypatch.setattr(cli, "PreMatchResearchScoringService", FakeService)

    payload = cli.run_world_cup_prediction(
        db_path=db_path,
        output_dir=tmp_path / "bundle",
        fixture_ids=["fixture_wc2026_open_1"],
    )

    assert calls
    assert calls[0]["fixture_ids"] == ["fixture_wc2026_open_1"]
    assert payload["request"]["backfill"] == "ok"
    assert payload["status"] == "ok"


def test_prediction_cli_reports_backfill_and_routing_gaps(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "research.db"
    _seed_fixture(db_path)

    def fake_backfill(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "partial",
            "data_quality": {
                "recent_results": "ok",
                "player_form": "missing",
                "odds": "missing",
            },
            "source": {
                "research_provider": "skip",
                "odds_provider": "skip",
                "route": {
                    "research": {"reason": "no_research_provider_available"},
                    "odds": {"reason": "no_odds_provider_available"},
                },
            },
        }

    class FakeService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_prediction(self, fixture_id: str) -> dict[str, Any]:
            return {
                "fixture_id": fixture_id,
                "home_team": "Home FC",
                "away_team": "Away FC",
                "probabilities": {
                    "home_win": 0.45,
                    "draw": 0.28,
                    "away_win": 0.27,
                    "over_2_5": 0.52,
                },
                "risk": {"level": "high", "confidence": 45},
                "coverage": {
                    "status": "partial",
                    "unavailable_components": ["key_player_status"],
                    "partial_components": ["odds_movement"],
                },
                "calibration": {"applied": False},
                "prediction_routing": {
                    "totals": {"status": "unavailable", "reason_code": "attack_defense_inputs_unavailable"},
                    "btts": {"status": "unavailable", "reason_code": "attack_defense_inputs_unavailable"},
                    "scoreline": {"status": "unavailable", "reason_code": "attack_defense_inputs_unavailable"},
                },
                "recommended_scores": [],
            }

    monkeypatch.setattr(cli, "run_targeted_backfill", fake_backfill)
    monkeypatch.setattr(cli, "PreMatchResearchScoringService", FakeService)

    payload = cli.run_world_cup_prediction(
        db_path=db_path,
        output_dir=tmp_path / "bundle",
        fixture_ids=["fixture_wc2026_open_1"],
    )

    gaps = payload["predictions"][0]["gaps"]
    assert payload["status"] == "partial"
    assert "data_quality:player_form:missing" in gaps
    assert "provider:research_provider:skip" in gaps
    assert "provider:odds:no_odds_provider_available" in gaps
    assert "unavailable_components:key_player_status" in gaps
    assert "scoreline:attack_defense_inputs_unavailable" in gaps


def test_prediction_cli_keeps_standard_failure_payload(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "research.db"
    _seed_fixture(db_path)

    class FailingService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def build_prediction(self, fixture_id: str) -> dict[str, Any]:
            raise ValueError(f"fixture_not_ready:{fixture_id}")

    monkeypatch.setattr(cli, "PreMatchResearchScoringService", FailingService)

    payload = cli.run_world_cup_prediction(
        db_path=db_path,
        output_dir=tmp_path / "bundle",
        fixture_ids=["fixture_wc2026_open_1"],
        run_backfill=False,
    )

    assert payload["status"] == "failed"
    assert payload["predictions"][0]["data_status"] == "failed"
    assert payload["predictions"][0]["gaps"] == ["fixture_not_ready:fixture_wc2026_open_1"]
