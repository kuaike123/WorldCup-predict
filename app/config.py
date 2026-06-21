from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_THE_ODDS_API_MARKETS = "h2h,totals,spreads"


def _dotenv_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_wrapping_quotes(value.strip())
        if key:
            values[key] = value
    return values


def _runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    env_file = Path(env.get("WCA_ENV_FILE", str(ROOT / ".env")))
    for key, value in _dotenv_values(env_file).items():
        env.setdefault(key, value)
    return env


def _split_command_args(value: str) -> tuple[str, ...]:
    value = value.strip()
    if not value:
        return ()
    return tuple(_strip_wrapping_quotes(part) for part in shlex.split(value, posix=False))


def _resolve_command_args(base_dir: Path, args: tuple[str, ...]) -> tuple[str, ...]:
    resolved: list[str] = []
    for arg in args:
        candidate = Path(arg)
        if candidate.is_absolute():
            resolved.append(str(candidate))
            continue
        if candidate.suffix and not arg.startswith("-"):
            local_candidate = base_dir / candidate
            if local_candidate.exists():
                resolved.append(str(local_candidate))
                continue
        resolved.append(arg)
    return tuple(resolved)


def _split_env_list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _looks_like_python_command(value: str) -> bool:
    command_name = Path(_strip_wrapping_quotes(value)).name.casefold()
    return command_name.startswith("python") or command_name in {"py", "py.exe"}


def _default_crawler_python_path(command_path: str) -> str:
    if command_path and _looks_like_python_command(command_path):
        return command_path
    return ""


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


@dataclass(frozen=True)
class Settings:
    service_name: str = "world-cup-agent"
    phase: str = "MVP1-P2"
    sample_dir: Path = ROOT / "data" / "samples"
    store_path: Path = ROOT / "outputs" / "p0_local_store.json"
    research_db_path: Path = ROOT / "outputs" / "research_local.db"
    research_db_enabled: bool = True
    data_source_mode: str = "mock"
    data_source_match_provider: str = "sportradar_soccer"
    data_source_odds_provider: str = "auto"
    data_source_live_provider: str = "mock_live"
    data_source_research_provider: str = "auto"
    data_source_crawler_provider: str = "external_crawler"
    enable_crawler: bool = True
    live_data_mode: str = "mock"
    live_data_source_reliability: float = 0.85
    live_stale_after_seconds: int = 120
    live_signal_window_minutes: int = 15
    live_signal_min_quality: float = 0.60
    live_signal_low_quality_confidence_multiplier: float = 0.50
    live_refresh_interval_seconds: int = 120
    live_alert_min_confidence: float = 0.60
    live_alert_min_quality: float = 0.60
    live_alert_cooldown_seconds: int = 300
    live_alert_allowed_risk_levels: tuple[str, ...] = ("medium", "high")
    live_auto_discovery_enabled: bool = True
    live_auto_discovery_lookback_minutes: int = 180
    live_auto_discovery_lookahead_minutes: int = 30
    live_auto_refresh_matches_enabled: bool = False
    live_crawler_supplement_enabled: bool = True
    live_crawler_target_urls: tuple[str, ...] = ()
    api_sports_key: str = ""
    api_sports_base_url: str = "https://v3.football.api-sports.io"
    api_sports_world_cup_league_id: str = "1"
    api_sports_world_cup_season: str = "2026"
    the_odds_api_key: str = ""
    the_odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    the_odds_api_sport_key: str = "soccer_fifa_world_cup"
    the_odds_api_regions: str = "eu"
    the_odds_api_markets: str = DEFAULT_THE_ODDS_API_MARKETS
    the_odds_api_odds_format: str = "decimal"
    the_odds_api_bookmakers: str = ""
    crawler_command_path: str = ""
    crawler_python_path: str = ""
    sports_stable_crawl_scripts_dir: Path = ROOT / "vendor" / "sports-stable-crawl" / "scripts"
    crawler_command_args: tuple[str, ...] = ()
    crawler_timeout_seconds: float = 15.0
    targeted_backfill_crawler_timeout_seconds: float = 600.0
    sportradar_soccer_api_key: str = ""
    sportradar_soccer_access_level: str = "trial"
    sportradar_soccer_language: str = "en"
    sportradar_soccer_base_url: str = "https://api.sportradar.com/soccer"
    sportradar_soccer_extended_base_url: str = "https://api.sportradar.com/soccer-extended"
    sportradar_soccer_extended_enabled: bool = False
    sportradar_soccer_timeout_seconds: float = 5.0
    sportradar_soccer_world_cup_competition_id: str = ""
    sportradar_soccer_world_cup_season_id: str = ""
    bot_provider: str = "mock"
    llm_expression_enabled: bool = False
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_endpoint_mode: str = "responses"
    llm_timeout_seconds: float = 8.0
    llm_max_retries: int = 0
    feishu_bot_app_id: str = ""
    feishu_bot_app_secret: str = ""
    feishu_bot_verification_token: str = ""
    feishu_bot_receive_id_type: str = "chat_id"
    feishu_bot_long_connection_enabled: bool = False
    feishu_bot_free_chat_id: str = ""
    feishu_bot_vip_chat_id: str = ""
    telegram_bot_token: str = ""
    scheduler_enabled: bool = False
    odds_refresh_interval_seconds: int = 300
    scheduler_job_max_matches: int = 20
    match_day_watch_interval_seconds: int = 300
    pre_match_crawler_interval_seconds: int = 300
    pre_match_crawler_graph_enabled: bool = False
    pre_match_crawler_graph_llm_enabled: bool = False
    formal_remediation_enabled: bool = False
    formal_remediation_interval_seconds: int = 300
    formal_remediation_max_attempts: int = 3
    match_day_watch_timezone: str = "Asia/Shanghai"
    match_day_watch_fixture_id_prefix: str = "fixture_wc2026_"
    match_day_watch_push_platform: str = "mock"
    match_day_watch_push_chat_id: str = ""
    match_day_watch_push_cooldown_seconds: int = 900
    match_day_watch_research_db_fallback_path: Path = ROOT / "outputs" / "research_p0_11_wc2026.db"
    odds_alert_percent_threshold: float = 0.05
    odds_alert_consecutive_moves: int = 2
    odds_alert_cooldown_seconds: int = 300


def load_settings() -> Settings:
    env = _runtime_env()
    sample_dir = Path(env.get("WCA_SAMPLE_DIR", str(ROOT / "data" / "samples")))
    store_path = Path(env.get("WCA_STORE_PATH", str(ROOT / "outputs" / "p0_local_store.json")))
    research_db_path = Path(env.get("WCA_RESEARCH_DB_PATH", str(ROOT / "outputs" / "research_local.db")))
    crawler_command_path = env.get("CRAWLER_COMMAND_PATH", "")
    return Settings(
        service_name=env.get("WCA_SERVICE_NAME", "world-cup-agent"),
        phase=env.get("WCA_PHASE", "MVP-V2-P2"),
        sample_dir=sample_dir,
        store_path=store_path,
        research_db_path=research_db_path,
        research_db_enabled=env.get("RESEARCH_DB_ENABLED", "true").lower() == "true",
        data_source_mode=env.get("DATA_SOURCE_MODE", "mock"),
        data_source_match_provider=env.get("DATA_SOURCE_MATCH_PROVIDER", "sportradar_soccer"),
        data_source_odds_provider=env.get(
            "DEFAULT_ODDS_PROVIDER",
            env.get("DATA_SOURCE_ODDS_PROVIDER", "auto"),
        ),
        data_source_live_provider=env.get("DATA_SOURCE_LIVE_PROVIDER", "mock_live"),
        data_source_research_provider=env.get(
            "DEFAULT_RESEARCH_PROVIDER",
            env.get("DATA_SOURCE_RESEARCH_PROVIDER", "auto"),
        ),
        data_source_crawler_provider=env.get("DATA_SOURCE_CRAWLER_PROVIDER", "external_crawler"),
        enable_crawler=env.get("ENABLE_CRAWLER", "true").lower() == "true",
        live_data_mode=env.get("LIVE_DATA_MODE", "mock"),
        live_data_source_reliability=float(env.get("LIVE_DATA_SOURCE_RELIABILITY", "0.85")),
        live_stale_after_seconds=int(env.get("LIVE_STALE_AFTER_SECONDS", "120")),
        live_signal_window_minutes=int(env.get("LIVE_SIGNAL_WINDOW_MINUTES", "15")),
        live_signal_min_quality=float(env.get("LIVE_SIGNAL_MIN_QUALITY", "0.60")),
        live_signal_low_quality_confidence_multiplier=float(
            env.get("LIVE_SIGNAL_LOW_QUALITY_CONFIDENCE_MULTIPLIER", "0.50")
        ),
        live_refresh_interval_seconds=int(env.get("LIVE_REFRESH_INTERVAL_SECONDS", "120")),
        live_alert_min_confidence=float(env.get("LIVE_ALERT_MIN_CONFIDENCE", "0.60")),
        live_alert_min_quality=float(env.get("LIVE_ALERT_MIN_QUALITY", "0.60")),
        live_alert_cooldown_seconds=int(env.get("LIVE_ALERT_COOLDOWN_SECONDS", "300")),
        live_alert_allowed_risk_levels=tuple(
            item.strip()
            for item in env.get("LIVE_ALERT_ALLOWED_RISK_LEVELS", "medium,high").split(",")
            if item.strip()
        ),
        live_auto_discovery_enabled=env.get("LIVE_AUTO_DISCOVERY_ENABLED", "true").lower() == "true",
        live_auto_discovery_lookback_minutes=int(env.get("LIVE_AUTO_DISCOVERY_LOOKBACK_MINUTES", "180")),
        live_auto_discovery_lookahead_minutes=int(env.get("LIVE_AUTO_DISCOVERY_LOOKAHEAD_MINUTES", "30")),
        live_auto_refresh_matches_enabled=env.get("LIVE_AUTO_REFRESH_MATCHES_ENABLED", "false").lower() == "true",
        live_crawler_supplement_enabled=env.get("LIVE_CRAWLER_SUPPLEMENT_ENABLED", "true").lower() == "true",
        live_crawler_target_urls=_split_env_list(env.get("LIVE_CRAWLER_TARGET_URLS", "")),
        api_sports_key=env.get("API_SPORTS_KEY", ""),
        api_sports_base_url=env.get("API_SPORTS_BASE_URL", "https://v3.football.api-sports.io"),
        api_sports_world_cup_league_id=env.get("API_SPORTS_WORLD_CUP_LEAGUE_ID", "1"),
        api_sports_world_cup_season=env.get("API_SPORTS_WORLD_CUP_SEASON", "2026"),
        the_odds_api_key=env.get("THE_ODDS_API_KEY", ""),
        the_odds_api_base_url=env.get("THE_ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4"),
        the_odds_api_sport_key=env.get("THE_ODDS_API_SPORT_KEY", "soccer_fifa_world_cup"),
        the_odds_api_regions=env.get("THE_ODDS_API_REGIONS", "eu"),
        the_odds_api_markets=env.get("THE_ODDS_API_MARKETS", DEFAULT_THE_ODDS_API_MARKETS),
        the_odds_api_odds_format=env.get("THE_ODDS_API_ODDS_FORMAT", "decimal"),
        the_odds_api_bookmakers=env.get("THE_ODDS_API_BOOKMAKERS", ""),
        crawler_command_path=crawler_command_path,
        crawler_python_path=env.get("CRAWLER_PYTHON_PATH", "") or _default_crawler_python_path(crawler_command_path),
        sports_stable_crawl_scripts_dir=Path(
            env.get(
                "SPORTS_STABLE_CRAWL_SCRIPTS_DIR",
                str(ROOT / "vendor" / "sports-stable-crawl" / "scripts"),
            )
        ),
        crawler_command_args=_resolve_command_args(ROOT, _split_command_args(env.get("CRAWLER_COMMAND_ARGS", ""))),
        crawler_timeout_seconds=float(env.get("CRAWLER_TIMEOUT_SECONDS", "15.0")),
        targeted_backfill_crawler_timeout_seconds=float(env.get("TARGETED_BACKFILL_CRAWLER_TIMEOUT_SECONDS", "600.0")),
        sportradar_soccer_api_key=env.get("SPORTRADAR_SOCCER_API_KEY", ""),
        sportradar_soccer_access_level=env.get("SPORTRADAR_SOCCER_ACCESS_LEVEL", "trial"),
        sportradar_soccer_language=env.get("SPORTRADAR_SOCCER_LANGUAGE", "en"),
        sportradar_soccer_base_url=env.get("SPORTRADAR_SOCCER_BASE_URL", "https://api.sportradar.com/soccer"),
        sportradar_soccer_extended_base_url=env.get(
            "SPORTRADAR_SOCCER_EXTENDED_BASE_URL",
            "https://api.sportradar.com/soccer-extended",
        ),
        sportradar_soccer_extended_enabled=(
            env.get("SPORTRADAR_SOCCER_EXTENDED_ENABLED", "false").lower() == "true"
        ),
        sportradar_soccer_timeout_seconds=float(env.get("SPORTRADAR_SOCCER_TIMEOUT_SECONDS", "5.0")),
        sportradar_soccer_world_cup_competition_id=env.get("SPORTRADAR_SOCCER_WORLD_CUP_COMPETITION_ID", ""),
        sportradar_soccer_world_cup_season_id=env.get("SPORTRADAR_SOCCER_WORLD_CUP_SEASON_ID", ""),
        bot_provider=env.get("BOT_PROVIDER", "mock"),
        llm_expression_enabled=env.get("LLM_EXPRESSION_ENABLED", "false").lower() == "true",
        llm_provider=env.get("LLM_PROVIDER", ""),
        llm_base_url=env.get("LLM_BASE_URL", ""),
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_model=env.get("LLM_MODEL", ""),
        llm_endpoint_mode=env.get("LLM_ENDPOINT_MODE", "responses"),
        llm_timeout_seconds=float(env.get("LLM_TIMEOUT_SECONDS", "8.0")),
        llm_max_retries=int(env.get("LLM_MAX_RETRIES", "0")),
        feishu_bot_app_id=env.get("FEISHU_BOT_APP_ID", ""),
        feishu_bot_app_secret=env.get("FEISHU_BOT_APP_SECRET", ""),
        feishu_bot_verification_token=env.get("FEISHU_BOT_VERIFICATION_TOKEN", ""),
        feishu_bot_receive_id_type=env.get("FEISHU_BOT_RECEIVE_ID_TYPE", "chat_id"),
        feishu_bot_long_connection_enabled=env.get("FEISHU_BOT_LONG_CONNECTION_ENABLED", "false").lower() == "true",
        feishu_bot_free_chat_id=env.get("FEISHU_BOT_FREE_CHAT_ID", ""),
        feishu_bot_vip_chat_id=env.get("FEISHU_BOT_VIP_CHAT_ID", ""),
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        scheduler_enabled=env.get("SCHEDULER_ENABLED", "false").lower() == "true",
        odds_refresh_interval_seconds=int(env.get("ODDS_REFRESH_INTERVAL_SECONDS", "300")),
        scheduler_job_max_matches=int(env.get("SCHEDULER_JOB_MAX_MATCHES", "20")),
        match_day_watch_interval_seconds=int(env.get("MATCH_DAY_WATCH_INTERVAL_SECONDS", "300")),
        pre_match_crawler_interval_seconds=int(env.get("PRE_MATCH_CRAWLER_INTERVAL_SECONDS", "300")),
        pre_match_crawler_graph_enabled=env.get("PRE_MATCH_CRAWLER_GRAPH_ENABLED", "false").lower() == "true",
        pre_match_crawler_graph_llm_enabled=env.get("PRE_MATCH_CRAWLER_GRAPH_LLM_ENABLED", "false").lower() == "true",
        formal_remediation_enabled=env.get("FORMAL_REMEDIATION_ENABLED", "false").lower() == "true",
        formal_remediation_interval_seconds=int(env.get("FORMAL_REMEDIATION_INTERVAL_SECONDS", "300")),
        formal_remediation_max_attempts=int(env.get("FORMAL_REMEDIATION_MAX_ATTEMPTS", "3")),
        match_day_watch_timezone=env.get("MATCH_DAY_WATCH_TIMEZONE", "Asia/Shanghai"),
        match_day_watch_fixture_id_prefix=env.get("MATCH_DAY_WATCH_FIXTURE_ID_PREFIX", "fixture_wc2026_"),
        match_day_watch_push_platform=env.get("MATCH_DAY_WATCH_PUSH_PLATFORM", "mock"),
        match_day_watch_push_chat_id=env.get("MATCH_DAY_WATCH_PUSH_CHAT_ID", ""),
        match_day_watch_push_cooldown_seconds=int(env.get("MATCH_DAY_WATCH_PUSH_COOLDOWN_SECONDS", "900")),
        match_day_watch_research_db_fallback_path=Path(
            env.get(
                "MATCH_DAY_WATCH_RESEARCH_DB_FALLBACK_PATH",
                str(ROOT / "outputs" / "research_p0_11_wc2026.db"),
            )
        ),
        odds_alert_percent_threshold=float(env.get("ODDS_ALERT_PERCENT_THRESHOLD", "0.05")),
        odds_alert_consecutive_moves=int(env.get("ODDS_ALERT_CONSECUTIVE_MOVES", "2")),
        odds_alert_cooldown_seconds=int(env.get("ODDS_ALERT_COOLDOWN_SECONDS", "300")),
    )
