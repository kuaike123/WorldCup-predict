from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.research_db.provider_contracts import BaseProvider, CrawlerProvider, ProviderResult
from app.research_db.provider_router import resolve_provider_route
from app.research_db.sportradar_soccer import SportradarSoccerAdapter, SportradarSoccerProvider
from app.research_db.world_cup_2026_odds import TheOddsApiProvider


def _crawler_scripts(tmp_path: Path) -> Path:
    scripts_dir = tmp_path / "crawler"
    scripts_dir.mkdir()
    (scripts_dir / "whoscored_workflow.py").write_text("# test research crawler\n", encoding="utf-8")
    (scripts_dir / "soccerway_odds.py").write_text("# test odds crawler\n", encoding="utf-8")
    return scripts_dir


def test_all_public_providers_implement_base_contract() -> None:
    providers = [
        SportradarSoccerProvider(Settings()),
        TheOddsApiProvider(Settings()),
        CrawlerProvider(),
    ]
    assert all(isinstance(provider, BaseProvider) for provider in providers)
    for provider in providers:
        assert callable(provider.get_recent_results)
        assert callable(provider.get_player_form)
        assert callable(provider.get_odds)


def test_sportradar_provider_reports_missing_config_without_network() -> None:
    provider = SportradarSoccerProvider(Settings(sportradar_soccer_api_key=""))
    recent = provider.get_recent_results("sr:competitor:1")
    player = provider.get_player_form("sr:player:1")
    odds = provider.get_odds("sr:sport_event:1")

    assert recent.status == "missing"
    assert recent.data == []
    assert recent.diagnostics["error_code"] == "missing_config"
    assert player.status == "missing"
    assert odds.status == "unsupported"


def test_sportradar_post_match_endpoints_report_configuration_without_network() -> None:
    adapter = SportradarSoccerAdapter(Settings(sportradar_soccer_api_key=""))

    assert adapter.fetch_sport_event_summary("sr:sport_event:1").error.code == "missing_config"
    assert adapter.fetch_sport_event_lineups("sr:sport_event:1").error.code == "missing_config"
    assert adapter.fetch_extended_sport_event_summary("sr:sport_event:1").error.code == "missing_config"


def test_the_odds_api_provider_reports_missing_config_without_network() -> None:
    provider = TheOddsApiProvider(Settings(the_odds_api_key=""))
    odds = provider.get_odds("event-1")

    assert odds.status == "missing"
    assert "missing_key" in odds.diagnostics["reason"]
    assert provider.get_recent_results("team-1").status == "unsupported"
    assert provider.get_player_form("player-1").status == "unsupported"


def test_crawler_provider_requires_explicit_fetcher_injection() -> None:
    provider = CrawlerProvider(
        recent_results_fetcher=lambda team_id: ProviderResult(
            status="ok",
            data=[{"team_id": team_id}],
        )
    )

    assert provider.get_recent_results("team-1").data == [{"team_id": "team-1"}]
    assert provider.get_player_form("player-1").status == "missing"
    assert provider.get_odds("fixture-1").status == "missing"


def test_source_mode_cannot_override_independent_provider_configuration(tmp_path) -> None:
    scripts_dir = _crawler_scripts(tmp_path)
    route = resolve_provider_route(
        settings=SimpleNamespace(
            data_source_research_provider="sportradar_soccer",
            data_source_odds_provider="crawler",
            sportradar_soccer_api_key="sr-key",
            the_odds_api_key="",
            enable_crawler=True,
        ),
        skill_scripts_dir=scripts_dir,
        legacy_source_mode="api",
    )

    assert route.research_provider == "sportradar_soccer"
    assert route.odds_provider == "crawler"
    assert route.legacy_source_mode == "api"
    assert route.as_dict()["legacy_source_mode_affects_selection"] is False


def test_crawler_mode_cannot_override_paid_odds_configuration(tmp_path) -> None:
    scripts_dir = _crawler_scripts(tmp_path)
    route = resolve_provider_route(
        settings=SimpleNamespace(
            data_source_research_provider="crawler",
            data_source_odds_provider="the_odds_api",
            sportradar_soccer_api_key="",
            the_odds_api_key="odds-key",
            enable_crawler=True,
        ),
        skill_scripts_dir=scripts_dir,
        legacy_source_mode="crawler",
    )

    assert route.research_provider == "crawler"
    assert route.odds_provider == "the_odds_api"


def test_missing_paid_keys_fall_back_to_installed_crawler(tmp_path) -> None:
    scripts_dir = _crawler_scripts(tmp_path)
    route = resolve_provider_route(
        settings=SimpleNamespace(
            data_source_research_provider="sportradar_soccer",
            data_source_odds_provider="the_odds_api",
            sportradar_soccer_api_key="",
            the_odds_api_key="",
            enable_crawler=True,
        ),
        skill_scripts_dir=scripts_dir,
    )

    assert route.research_provider == "crawler"
    assert route.research.fallback_used is True
    assert route.odds_provider == "crawler"
    assert route.odds.fallback_used is True


def test_disabled_or_uninstalled_crawler_is_explicitly_skipped(tmp_path) -> None:
    route = resolve_provider_route(
        settings=SimpleNamespace(
            data_source_research_provider="crawler",
            data_source_odds_provider="crawler",
            sportradar_soccer_api_key="",
            the_odds_api_key="",
            enable_crawler=False,
        ),
        skill_scripts_dir=tmp_path / "missing",
    )

    assert route.research_provider == "skip"
    assert route.research.reason == "crawler_unavailable"
    assert route.odds_provider == "skip"
    assert route.odds.reason == "crawler_unavailable"
