from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.research_db.post_match_sync import PostMatchSyncService
from app.research_db.pre_match_research_features import _player_form_score
from app.research_db.repository import POST_MATCH_LOOP_TABLES, ResearchDatabaseRepository
from src.scoring.pre_match_research_preview import (
    P0_15_COMPONENT_DIMENSIONS,
    P0_15_VERSION,
    P0_15_WEIGHTS_VERSION,
)


@dataclass(frozen=True)
class FakeResult:
    data: Any | None = None
    error: Any | None = None


class FakePostMatchProvider:
    source = "sportradar_soccer"

    def __init__(self, *, include_minutes: bool = True) -> None:
        self.include_minutes = include_minutes

    def fetch_sport_event_summary(self, sport_event_id: str) -> FakeResult:
        return FakeResult(
            data={
                "sport_event": {
                    "id": sport_event_id,
                    "start_time": "2026-06-20T20:00:00+00:00",
                    "competitors": [
                        {"id": "sr:competitor:home", "qualifier": "home"},
                        {"id": "sr:competitor:away", "qualifier": "away"},
                    ],
                },
                "sport_event_status": {
                    "status": "closed",
                    "home_score": 2,
                    "away_score": 1,
                },
            }
        )

    def fetch_sport_event_lineups(self, sport_event_id: str) -> FakeResult:
        return FakeResult(
            data={
                "lineups": {
                    "competitors": [
                        {
                            "id": "sr:competitor:home",
                            "qualifier": "home",
                            "players": [
                                {
                                    "id": "sr:player:home1",
                                    "name": "Home Player",
                                    "starter": True,
                                    "played": True,
                                    "position": "forward",
                                }
                            ],
                        },
                        {
                            "id": "sr:competitor:away",
                            "qualifier": "away",
                            "players": [
                                {
                                    "id": "sr:player:away1",
                                    "name": "Away Player",
                                    "starter": False,
                                    "played": True,
                                    "position": "midfielder",
                                }
                            ],
                        },
                    ]
                }
            }
        )

    def fetch_extended_sport_event_summary(self, sport_event_id: str) -> FakeResult:
        players: list[dict[str, Any]] = []
        if self.include_minutes:
            players = [
                {
                    "player": {"id": "sr:player:home1"},
                    "statistics": {"minutes_played": 90},
                },
                {
                    "player": {"id": "sr:player:away1"},
                    "statistics": {"minutes_played": 20},
                },
            ]
        return FakeResult(
            data={
                "statistics": {
                    "totals": {
                        "competitors": [{"players": players}]
                    }
                }
            }
        )


def _seed_repository(path: Path) -> ResearchDatabaseRepository:
    repository = ResearchDatabaseRepository(path)
    repository.upsert_facts(
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
            "players": [
                {
                    "player_id": "player_home",
                    "canonical_name": "Home Player",
                    "team_id": "team_home",
                    "source": "sportradar_soccer",
                    "source_player_id": "sr:player:home1",
                    "available_at": "2026-06-01T00:00:00+00:00",
                },
                {
                    "player_id": "player_away",
                    "canonical_name": "Away Player",
                    "team_id": "team_away",
                    "source": "sportradar_soccer",
                    "source_player_id": "sr:player:away1",
                    "available_at": "2026-06-01T00:00:00+00:00",
                },
            ],
            "fixtures": [
                {
                    "fixture_id": "fixture_world_cup_1",
                    "competition": "World Cup",
                    "season": "2026",
                    "home_team_id": "team_home",
                    "away_team_id": "team_away",
                    "match_time": "2026-06-20T20:00:00+00:00",
                    "neutral_field": True,
                    "source": "sportradar_soccer",
                    "source_fixture_id": "sr:sport_event:1",
                    "available_at": "2026-06-01T00:00:00+00:00",
                }
            ],
        }
    )
    return repository


def _valid_prediction() -> dict[str, Any]:
    components = [
        {
            "dimension": dimension,
            "value": 0.0,
            "home_value": 50.0,
            "away_value": 50.0,
            "status": "ok",
            "quality_score": 1.0,
            "source_fields": [],
            "missing_reason": None,
        }
        for dimension in P0_15_COMPONENT_DIMENSIONS
    ]
    return {
        "version": P0_15_VERSION,
        "fixture_id": "fixture_world_cup_1",
        "match_id": "fixture_world_cup_1",
        "home_team": "Home FC",
        "away_team": "Away FC",
        "generated_at": "2026-06-20T17:00:00+00:00",
        "as_of": "2026-06-20T17:00:00+00:00",
        "weights_version": P0_15_WEIGHTS_VERSION,
        "not_used_in_production_scoring_by_default": True,
        "team_scores": {"home": 50.0, "away": 50.0, "score_gap": 0.0},
        "probabilities": {
            "home_win": 0.33,
            "draw": 0.34,
            "away_win": 0.33,
            "over_2_5": 0.5,
            "upset_risk": 0.2,
        },
        "probability_model_mode": "hybrid_routed",
        "prediction_routing": {
            "1x2": {
                "route": "legacy_logistic",
                "status": "available",
                "probability_keys": ["home_win", "draw", "away_win"],
            },
            "totals": {
                "route": "independent_poisson",
                "status": "unavailable",
                "reason_code": "sample_prediction_without_scoreline_route",
                "probability_keys": ["over_2_5", "under_2_5"],
            },
            "btts": {
                "route": "independent_poisson",
                "status": "unavailable",
                "reason_code": "sample_prediction_without_scoreline_route",
                "probability_keys": ["btts_yes", "btts_no"],
            },
            "scoreline": {
                "route": "independent_poisson",
                "status": "unavailable",
                "reason_code": "sample_prediction_without_scoreline_route",
            },
        },
        "risk": {"level": "medium", "confidence": 60.0},
        "components": components,
        "coverage": {"status": "ok"},
    }


def test_repository_tables_prediction_round_trip_and_appearance_cutoff(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path / "research.db")

    assert repository.initialize()["post_match_loop_tables_present"] is True
    assert repository.row_counts(POST_MATCH_LOOP_TABLES) == {
        "player_match_appearances": 0,
        "pre_match_predictions": 0,
    }

    stored = repository.save_pre_match_prediction(_valid_prediction())
    assert stored["prediction"]["probabilities"]["draw"] == 0.34
    repository.save_pre_match_prediction(_valid_prediction())
    assert repository.row_counts(POST_MATCH_LOOP_TABLES)["pre_match_predictions"] == 1

    repository.upsert_facts(
        {
            "player_match_appearances": [
                {
                    "appearance_id": "appearance_home",
                    "fixture_id": "fixture_world_cup_1",
                    "player_id": "player_home",
                    "team_id": "team_home",
                    "played_at": "2026-06-20T20:00:00+00:00",
                    "appeared": True,
                    "starter": True,
                    "minutes_played": 90,
                    "source": "sportradar_soccer",
                    "source_appearance_id": "sr:sport_event:1:sr:player:home1",
                    "available_at": "2026-06-20T22:00:00+00:00",
                }
            ]
        }
    )
    assert repository.latest_player_match_appearance(
        "player_home",
        before_played_at="2026-06-21T20:00:00+00:00",
        available_at_cutoff="2026-06-21T17:00:00+00:00",
    )["minutes_played"] == 90
    assert repository.latest_player_match_appearance(
        "player_home",
        before_played_at="2026-06-20T19:00:00+00:00",
        available_at_cutoff="2026-06-21T17:00:00+00:00",
    ) is None


def test_repository_requires_explicit_appeared(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path / "research.db")

    with pytest.raises(ValueError, match="player_match_appearance_appeared_required"):
        repository.upsert_player_match_appearance(
            {
                "appearance_id": "appearance_missing_status",
                "fixture_id": "fixture_world_cup_1",
                "player_id": "player_home",
                "team_id": "team_home",
                "played_at": "2026-06-20T20:00:00+00:00",
                "source": "sportradar_soccer",
                "source_appearance_id": "sr:sport_event:1:missing-status",
                "available_at": "2026-06-20T22:00:00+00:00",
            }
        )


def test_post_match_sync_is_dry_run_safe_and_idempotent(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path / "research.db")
    repository.save_pre_match_prediction(_valid_prediction())
    service = PostMatchSyncService(
        repository,
        FakePostMatchProvider(),
        now_fn=lambda: "2026-06-21T00:00:00+00:00",
    )

    dry_run = service.sync_recent_completed_matches(
        fixture_ids=["fixture_world_cup_1"],
        dry_run=True,
    )
    assert dry_run["fixtures"][0]["status"] == "ok"
    assert repository.get_match_result("fixture_world_cup_1") is None

    first = service.sync_recent_completed_matches(fixture_ids=["fixture_world_cup_1"])
    assert first["status"] == "ok"
    assert first["fixtures"][0]["data_quality"] == {
        "result": "ok",
        "lineup": "ok",
        "player_appearances": "ok",
        "prediction_snapshot": "ok",
        "post_match_review": "skipped",
    }
    appearance_by_player = {
        row["player_id"]: row
        for row in repository.list_player_match_appearances(
            fixture_id="fixture_world_cup_1"
        )
    }
    assert appearance_by_player["player_home"]["starter"] is True
    assert appearance_by_player["player_home"]["minutes_played"] == 90
    assert appearance_by_player["player_away"]["starter"] is False
    assert appearance_by_player["player_away"]["minutes_played"] == 20

    second = service.sync_recent_completed_matches(fixture_ids=["fixture_world_cup_1"])
    assert second["fixtures"][0]["status"] == "skipped"
    assert len(repository.list_player_match_appearances(fixture_id="fixture_world_cup_1")) == 2


def test_missing_player_minutes_remain_null_and_report_partial(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path / "research.db")
    report = PostMatchSyncService(
        repository,
        FakePostMatchProvider(include_minutes=False),
        now_fn=lambda: "2026-06-21T00:00:00+00:00",
    ).sync_recent_completed_matches(fixture_ids=["fixture_world_cup_1"])

    fixture_report = report["fixtures"][0]
    assert fixture_report["status"] == "partial"
    assert fixture_report["data_quality"]["player_appearances"] == "partial"
    rows = repository.list_player_match_appearances(fixture_id="fixture_world_cup_1")
    assert rows
    assert all(row["minutes_played"] is None for row in rows)


def test_partial_appearance_rows_are_retried_until_complete(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path / "research.db")
    partial_service = PostMatchSyncService(
        repository,
        FakePostMatchProvider(include_minutes=False),
        now_fn=lambda: "2026-06-21T00:00:00+00:00",
    )
    complete_service = PostMatchSyncService(
        repository,
        FakePostMatchProvider(include_minutes=True),
        now_fn=lambda: "2026-06-21T00:10:00+00:00",
    )

    first = partial_service.sync_recent_completed_matches(
        fixture_ids=["fixture_world_cup_1"]
    )
    second = complete_service.sync_recent_completed_matches(
        fixture_ids=["fixture_world_cup_1"]
    )

    assert first["fixtures"][0]["status"] == "partial"
    assert second["fixtures"][0]["status"] == "ok"
    assert second["fixtures"][0]["status"] != "skipped"
    rows = repository.list_player_match_appearances(fixture_id="fixture_world_cup_1")
    assert {row["minutes_played"] for row in rows} == {20, 90}


def test_key_player_70_30_proxy_and_actual_appearance_scores_are_locked() -> None:
    regular_starter_proxy = _player_form_score(
        {
            "club_name": "Sample FC",
            "club_recent_matches": 5,
            "club_recent_minutes": 380,
            "club_recent_starts": 5,
            "national_recent_minutes": 900,
            "national_recent_starts": 10,
        },
        team_labels={"sample"},
    )
    low_usage_substitute_proxy = _player_form_score(
        {
            "club_name": "Sample FC",
            "club_recent_matches": 5,
            "club_recent_minutes": 70,
            "club_recent_starts": 0,
            "national_recent_minutes": 900,
            "national_recent_starts": 10,
        },
        team_labels={"sample"},
    )
    actual_substitute = _player_form_score(
        {
            "club_name": "Sample FC",
            "club_recent_matches": 5,
            "club_recent_minutes": 380,
            "club_recent_starts": 5,
            "national_recent_minutes": 900,
            "national_recent_starts": 10,
            "last_match_status_source": "actual_appearance",
            "last_match_appeared": True,
            "last_match_started": False,
            "last_match_minutes": 20,
        },
        team_labels={"sample"},
    )

    assert regular_starter_proxy == 96.19
    assert low_usage_substitute_proxy == 42.81
    assert actual_substitute == 73.69
