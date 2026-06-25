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
                },
                "risk": {"level": "medium", "confidence": 61},
                "coverage": {"status": "partial"},
                "calibration": {"applied": False},
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
    assert "unsupported_probability:btts_yes" in prediction["gaps"]
    assert "fixture_id" in payload["output_contract"]["required_per_fixture_fields"]


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
