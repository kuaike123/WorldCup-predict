import json
import subprocess
from app.research_db.features import HistoricalFeatureExtractor
from app.research_db import run_targeted_backfill
from app.research_db import world_cup_research_backfill as backfill_module
from app.research_db.world_cup_2026_recent_results import DEFAULT_SKILL_SCRIPTS_DIR
from app.research_db.world_cup_research_backfill import (
    _build_sportradar_player_form_snapshot,
    _sportradar_recent_matches,
    resolve_backfill_source_mode,
    resolve_crawler_runtime_settings,
    resolve_odds_backfill_provider,
    resolve_research_backfill_provider,
)
from app.research_db.world_cup_2026_odds import _extract_two_way_line_market, _select_bookmaker
from pathlib import Path
from types import SimpleNamespace


def test_public_paths_are_not_machine_private() -> None:
    assert callable(run_targeted_backfill)
    assert "\\Users\\" not in str(DEFAULT_SKILL_SCRIPTS_DIR)
    assert str(DEFAULT_SKILL_SCRIPTS_DIR).endswith("vendor\\sports-stable-crawl\\scripts")


def test_plugin_manifests_use_repo_local_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    codex_plugin = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    claude_plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    codex_marketplace = json.loads((root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    claude_marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))

    assert codex_plugin["skills"] == "./skills/"
    assert claude_plugin["skills"] == "./skills/"
    assert codex_marketplace["plugins"][0]["source"]["path"] == "./"
    assert claude_marketplace["plugins"][0]["source"] == "./"
    assert (root / codex_plugin["skills"].removeprefix("./")).is_dir()
    assert (root / claude_plugin["skills"].removeprefix("./")).is_dir()
    assert (root / ".codex-plugin" / "plugin.json").is_file()
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    assert (root / ".agents" / "plugins" / "marketplace.json").is_file()
    assert (root / ".claude-plugin" / "marketplace.json").is_file()


def test_plugin_manifests_keep_release_metadata_in_sync() -> None:
    root = Path(__file__).resolve().parents[1]
    codex_plugin = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    claude_plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    codex_marketplace = json.loads((root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    claude_marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))

    codex_listing = codex_marketplace["plugins"][0]
    claude_listing = claude_marketplace["plugins"][0]

    assert codex_plugin["name"] == claude_plugin["name"] == codex_listing["name"] == claude_listing["name"]
    assert codex_plugin["version"] == claude_plugin["version"] == claude_listing["version"]
    assert codex_plugin["interface"]["displayName"] == claude_plugin["displayName"] == codex_marketplace["interface"]["displayName"] == claude_listing["displayName"]


def test_repo_root_is_the_only_layout_that_satisfies_local_marketplace_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    codex_marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
    claude_marketplace_path = root / ".claude-plugin" / "marketplace.json"

    codex_repo_root = root
    claude_repo_root = root
    codex_marketplace_dir = codex_marketplace_path.parent
    claude_marketplace_dir = claude_marketplace_path.parent

    assert (codex_repo_root / ".codex-plugin" / "plugin.json").is_file()
    assert (claude_repo_root / ".claude-plugin" / "plugin.json").is_file()
    assert (codex_repo_root / "skills").is_dir()
    assert (claude_repo_root / "skills").is_dir()

    assert not (codex_marketplace_dir / ".codex-plugin" / "plugin.json").exists()
    assert not (claude_marketplace_dir / ".claude-plugin" / "plugin.json").exists()
    assert not (codex_marketplace_dir / "skills").exists()
    assert not (claude_marketplace_dir / "skills").exists()


def test_crawler_python_override_is_documented_in_release_facing_skill_files() -> None:
    root = Path(__file__).resolve().parents[1]
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    skill_doc = (root / "skills" / "world-cup-research-backfill" / "SKILL.md").read_text(encoding="utf-8")
    source_policy = (
        root
        / "skills"
        / "world-cup-research-backfill"
        / "references"
        / "source-policy.md"
    ).read_text(encoding="utf-8")

    assert "CRAWLER_PYTHON_PATH=" in env_example
    assert "--crawler-python-path" in skill_doc
    assert "CRAWLER_PYTHON_PATH" in skill_doc
    assert "CRAWLER_PYTHON_PATH" in source_policy


def test_installed_console_entrypoint_exposes_documented_release_options() -> None:
    completed = subprocess.run(
        ["world-cup-research-backfill", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    help_text = completed.stdout
    assert "--fixture-id" in help_text
    assert "--local-date" in help_text
    assert "--source-mode {auto,api,crawler}" in help_text
    assert "--skill-scripts-dir" in help_text
    assert "--crawler-python-path" in help_text
    assert "--no-resume-existing" in help_text


def test_backfill_writes_error_summary_on_runtime_failure(tmp_path, monkeypatch) -> None:
    class _FakeRepository:
        def __init__(self, db_path):
            self.db_path = db_path

        def get_fixture(self, fixture_id: str):
            return {
                "fixture_id": fixture_id,
                "match_time": "2026-06-12T19:00:00+00:00",
                "home_team_id": "team_a",
                "away_team_id": "team_b",
                "competition": "2026 FIFA World Cup",
            }

        def get_team(self, team_id: str):
            return {
                "team_id": team_id,
                "canonical_name": team_id,
                "country_code": "",
                "fifa_code": "",
                "source_team_id": team_id,
            }

    async def _raise_odds(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        backfill_module,
        "load_settings",
        lambda: SimpleNamespace(
            the_odds_api_key="live-key",
            sportradar_soccer_api_key="",
            data_source_research_provider="sportradar_soccer",
            data_source_odds_provider="the_odds_api",
            crawler_python_path="",
            targeted_backfill_crawler_timeout_seconds=600.0,
        ),
    )
    monkeypatch.setattr(backfill_module, "ResearchDatabaseRepository", _FakeRepository)
    monkeypatch.setattr(backfill_module, "_prepare_target_bundle", lambda **kwargs: kwargs["output_dir"].mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(backfill_module, "collect_world_cup_odds", _raise_odds)

    output_dir = tmp_path / "probe"
    try:
        backfill_module.run_targeted_backfill(
            db_path=tmp_path / "research.db",
            output_dir=output_dir,
            fixture_ids=["fixture_wc2026_66456916"],
            source_mode="api",
            resume_existing=False,
        )
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected runtime failure")

    summary = json.loads((output_dir / "targeted_backfill_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert summary["data_quality"] == {
        "recent_results": "missing",
        "player_form": "missing",
        "odds": "missing",
    }
    assert summary["data"] == {
        "recent_results": [],
        "player_form": [],
        "odds": [],
    }
    assert summary["source"]["research_provider"] == "skip"
    assert summary["source"]["odds_provider"] == "the_odds_api"
    assert summary["failed_step"] == "odds"
    assert summary["research_provider"] == "skip"
    assert summary["odds_provider"] == "the_odds_api"
    assert summary["recent_results"]["reason"] == "research_provider_unavailable"
    assert summary["player_form"]["reason"] == "research_provider_unavailable"
    assert summary["error"]["message"] == "boom"


def test_backfill_writes_error_summary_when_prepare_bundle_fails(tmp_path, monkeypatch) -> None:
    class _FakeRepository:
        def __init__(self, db_path):
            self.db_path = db_path

        def get_fixture(self, fixture_id: str):
            return {
                "fixture_id": fixture_id,
                "match_time": "2026-06-12T19:00:00+00:00",
                "home_team_id": "team_a",
                "away_team_id": "team_b",
                "competition": "2026 FIFA World Cup",
            }

        def get_team(self, team_id: str):
            return {
                "team_id": team_id,
                "canonical_name": team_id,
                "country_code": "",
                "fifa_code": "",
                "source_team_id": team_id,
            }

    monkeypatch.setattr(
        backfill_module,
        "load_settings",
        lambda: SimpleNamespace(
            the_odds_api_key="live-key",
            sportradar_soccer_api_key="",
            data_source_research_provider="sportradar_soccer",
            data_source_odds_provider="the_odds_api",
            crawler_python_path="",
            targeted_backfill_crawler_timeout_seconds=600.0,
        ),
    )
    monkeypatch.setattr(backfill_module, "ResearchDatabaseRepository", _FakeRepository)
    monkeypatch.setattr(backfill_module, "_prepare_target_bundle", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("prep failed")))

    output_dir = tmp_path / "prepare-fail"
    try:
        backfill_module.run_targeted_backfill(
            db_path=tmp_path / "research.db",
            output_dir=output_dir,
            fixture_ids=["fixture_wc2026_66456916"],
            source_mode="api",
            resume_existing=False,
        )
    except RuntimeError as exc:
        assert str(exc) == "prep failed"
    else:
        raise AssertionError("expected prepare bundle failure")

    summary = json.loads((output_dir / "targeted_backfill_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert set(summary["data_quality"]) == {"recent_results", "player_form", "odds"}
    assert all(isinstance(summary["data"][key], list) for key in summary["data_quality"])
    assert summary["failed_step"] == "prepare_target_bundle"
    assert summary["error"]["message"] == "prep failed"
    assert summary["recent_results"]["status"] == "pending"


class _FakeRepo:
    def recent_results_for_team(self, team_id: str, *, match_time: str, available_at_cutoff: str, limit: int):
        return [
            {
                "home_team_id": team_id,
                "away_team_id": "team_b",
                "home_score": 1,
                "away_score": 0,
                "competition": "FIFA World Cup",
                "played_at": "2026-06-01T00:00:00+00:00",
                "match_time": "2026-06-01T00:00:00+00:00",
                "source_result_id": "r1",
                "result_id": "r1",
                "available_at": "2026-06-01T00:00:00+00:00",
            },
            {
                "home_team_id": team_id,
                "away_team_id": "team_c",
                "home_score": 0,
                "away_score": 0,
                "competition": "Friendly",
                "played_at": "2026-05-28T00:00:00+00:00",
                "match_time": "2026-05-28T00:00:00+00:00",
                "source_result_id": "r2",
                "result_id": "r2",
                "available_at": "2026-05-28T00:00:00+00:00",
            },
        ][:limit]

    def blocked_results_after_cutoff(self, team_id: str, *, match_time: str, available_at_cutoff: str):
        return []


def test_weighted_recent_form_and_friendly_ratio_are_exposed() -> None:
    features = HistoricalFeatureExtractor(_FakeRepo()).extract_team_features(
        "team_a",
        match_time="2026-06-10T00:00:00+00:00",
        available_at_cutoff="2026-06-10T00:00:00+00:00",
    )["features"]
    assert features["recent_points_per_game"] == 2.429
    assert features["friendly_match_ratio"] == 0.286


def test_backfill_source_mode_is_compatibility_metadata_only() -> None:
    existing_dir = Path(__file__).resolve().parent
    assert resolve_backfill_source_mode(None, the_odds_api_key="k", skill_scripts_dir=existing_dir) == "auto"
    assert resolve_backfill_source_mode("api", the_odds_api_key="", skill_scripts_dir=existing_dir) == "api"
    assert resolve_backfill_source_mode("crawler", the_odds_api_key="k", skill_scripts_dir=existing_dir) == "crawler"


def test_backfill_source_mode_rejects_unknown_values() -> None:
    missing_dir = DEFAULT_SKILL_SCRIPTS_DIR / "__missing__"
    try:
        resolve_backfill_source_mode("hybrid", the_odds_api_key="", skill_scripts_dir=missing_dir)
    except ValueError as exc:
        assert "source_mode" in str(exc)
    else:
        raise AssertionError("expected invalid compatibility mode error")


def test_research_provider_prefers_sportradar_when_key_is_configured() -> None:
    provider = resolve_research_backfill_provider(
        "api",
        settings=SimpleNamespace(
            data_source_research_provider="sportradar_soccer",
            sportradar_soccer_api_key="sr-key",
        ),
        skill_scripts_dir=Path(__file__).resolve().parent / "__missing__",
    )
    assert provider == "sportradar_soccer"


def test_research_provider_can_fall_back_to_skip_in_api_mode() -> None:
    provider = resolve_research_backfill_provider(
        "api",
        settings=SimpleNamespace(
            data_source_research_provider="sportradar_soccer",
            sportradar_soccer_api_key="",
        ),
        skill_scripts_dir=Path(__file__).resolve().parent / "__missing__",
    )
    assert provider == "skip"


def test_odds_provider_respects_configured_api_path() -> None:
    provider = resolve_odds_backfill_provider(
        "auto",
        settings=SimpleNamespace(
            data_source_odds_provider="the_odds_api",
            the_odds_api_key="odds-key",
        ),
        skill_scripts_dir=Path(__file__).resolve().parent / "__missing__",
    )
    assert provider == "the_odds_api"


def test_api_mode_respects_configured_crawler_research_provider(tmp_path) -> None:
    scripts_dir = tmp_path / "crawler"
    scripts_dir.mkdir()
    (scripts_dir / "whoscored_workflow.py").write_text("# test crawler\n", encoding="utf-8")
    provider = resolve_research_backfill_provider(
        "api",
        settings=SimpleNamespace(
            data_source_research_provider="external_crawler",
            sportradar_soccer_api_key="",
            enable_crawler=True,
        ),
        skill_scripts_dir=scripts_dir,
    )
    assert provider == "crawler"


def test_sportradar_recent_match_parser_keeps_only_closed_matches_before_cutoff() -> None:
    matches = _sportradar_recent_matches(
        {
            "schedules": [
                {
                    "sport_event": {
                        "id": "sr:sport_event:1",
                        "start_time": "2026-06-10T19:00:00+00:00",
                        "sport_event_context": {"competition": {"name": "Friendly"}},
                        "competitors": [
                            {"id": "sr:competitor:100", "name": "Canada", "qualifier": "home"},
                            {"id": "sr:competitor:200", "name": "Japan", "qualifier": "away"},
                        ],
                    },
                    "sport_event_status": {"status": "closed", "home_score": 2, "away_score": 1},
                },
                {
                    "sport_event": {
                        "id": "sr:sport_event:2",
                        "start_time": "2026-06-12T19:00:00+00:00",
                        "competitors": [
                            {"id": "sr:competitor:100", "name": "Canada", "qualifier": "home"},
                            {"id": "sr:competitor:300", "name": "Mexico", "qualifier": "away"},
                        ],
                    },
                    "sport_event_status": {"status": "scheduled", "home_score": 0, "away_score": 0},
                },
            ]
        },
        competitor_id="sr:competitor:100",
        accessed_at="2026-06-11T12:00:00+00:00",
        limit=5,
    )
    assert len(matches) == 1
    assert matches[0]["source_fixture_id"] == "sr:sport_event:1"


def test_sportradar_player_form_snapshot_filters_player_specific_stats() -> None:
    snapshot = _build_sportradar_player_form_snapshot(
        {
            "player_id": "player_can_1",
            "team_id": "team_canada",
            "club_name": "Club A",
            "club_source_id": "club_a",
            "source_player_id": "fantasy_player_1",
        },
        summary_payload={
            "summaries": [
                {
                    "sport_event": {
                        "id": "sr:sport_event:n1",
                        "start_time": "2026-06-08T19:00:00+00:00",
                        "competitors": [
                            {"id": "sr:competitor:100", "name": "Canada", "qualifier": "home"},
                            {"id": "sr:competitor:200", "name": "Japan", "qualifier": "away"},
                        ],
                    },
                    "statistics": {
                        "totals": {
                            "competitors": [
                                {
                                    "players": [
                                        {"id": "sr:player:other", "statistics": {"minutes_played": 12, "goals_scored": 9}},
                                        {"id": "sr:player:target", "statistics": {"minutes_played": 81, "goals_scored": 1, "assists": 1, "starter": True}},
                                    ]
                                }
                            ]
                        }
                    },
                }
            ]
        },
        competitor_id="sr:competitor:100",
        accessed_at="2026-06-11T12:00:00+00:00",
        recent_club_match_limit=5,
        source_player_id="sr:player:target",
    )
    assert snapshot["national_recent_caps"] == 1
    assert snapshot["national_recent_minutes"] == 81
    assert snapshot["national_recent_goals"] == 1
    assert snapshot["national_recent_assists"] == 1


def test_api_mode_can_keep_research_steps_when_sportradar_is_available(tmp_path, monkeypatch) -> None:
    class _FakeRepository:
        def __init__(self, db_path):
            self.db_path = db_path

        def get_fixture(self, fixture_id: str):
            return {
                "fixture_id": fixture_id,
                "match_time": "2026-06-12T19:00:00+00:00",
                "home_team_id": "team_a",
                "away_team_id": "team_b",
                "competition": "2026 FIFA World Cup",
            }

        def get_team(self, team_id: str):
            return {
                "team_id": team_id,
                "canonical_name": team_id,
                "country_code": "",
                "fifa_code": "",
                "source_team_id": f"sr:competitor:{team_id}",
            }

    async def _raise_odds(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        backfill_module,
        "load_settings",
        lambda: SimpleNamespace(
            the_odds_api_key="live-key",
            sportradar_soccer_api_key="sr-key",
            data_source_research_provider="sportradar_soccer",
            data_source_odds_provider="the_odds_api",
            crawler_python_path="",
            targeted_backfill_crawler_timeout_seconds=600.0,
        ),
    )
    monkeypatch.setattr(backfill_module, "ResearchDatabaseRepository", _FakeRepository)
    monkeypatch.setattr(backfill_module, "_prepare_target_bundle", lambda **kwargs: kwargs["output_dir"].mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(backfill_module, "_collect_recent_results", lambda **kwargs: {"status": "ok", "step": "recent_results"})
    monkeypatch.setattr(backfill_module, "_collect_player_form", lambda **kwargs: {"status": "ok", "step": "player_form"})
    monkeypatch.setattr(backfill_module, "collect_world_cup_odds", _raise_odds)

    output_dir = tmp_path / "probe-sr"
    try:
        backfill_module.run_targeted_backfill(
            db_path=tmp_path / "research.db",
            output_dir=output_dir,
            fixture_ids=["fixture_wc2026_66456916"],
            source_mode="api",
            resume_existing=False,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected runtime failure")

    summary = json.loads((output_dir / "targeted_backfill_summary.json").read_text(encoding="utf-8"))
    assert summary["research_provider"] == "sportradar_soccer"
    assert summary["recent_results"]["status"] == "ok"
    assert summary["player_form"]["status"] == "ok"


def test_crawler_runtime_settings_fall_back_to_env_backfill_values() -> None:
    settings = SimpleNamespace(
        crawler_python_path="python-crawler",
        targeted_backfill_crawler_timeout_seconds=321.0,
    )
    python_path, timeout_seconds = resolve_crawler_runtime_settings(
        requested_python_path=None,
        requested_timeout_seconds=None,
        settings=settings,
    )
    assert python_path == "python-crawler"
    assert timeout_seconds == 321.0


def test_select_bookmaker_prefers_configured_key() -> None:
    bookmaker = _select_bookmaker(
        {
            "bookmakers": [
                {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h"}]},
                {"key": "pinnacle", "title": "Pinnacle", "markets": [{"key": "h2h"}, {"key": "spreads"}, {"key": "totals"}]},
            ]
        },
        preferred_bookmaker_keys=("pinnacle",),
    )
    assert bookmaker["key"] == "pinnacle"


def test_two_way_line_market_prefers_complete_line_before_partial_line() -> None:
    market = _extract_two_way_line_market(
        {
            "outcomes": [
                {"name": "Over", "point": 2.5, "price": 1.8},
                {"name": "Over", "point": 3.5, "price": 2.0},
                {"name": "Under", "point": 3.5, "price": 1.9},
            ]
        },
        left_name="Over",
        right_name="Under",
        left_key="over",
        right_key="under",
    )
    assert market["selected_line"]["handicap"] == 3.5
