from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import ROOT, load_settings
from app.research_db.pre_match_research_scoring import PreMatchResearchScoringService
from app.storage.json_store import JsonStore
from app.storage.repository import LocalRepository

from .repository import ResearchDatabaseRepository
from app.world_cup_targets import is_world_cup_2026_fixture
from .world_cup_2026_bootstrap import (
    COMPETITION_NAME,
    CORE_PLAYER_FIELDNAMES,
    CORE_PLAYER_SOURCE,
    FIXTURE_FIELDNAMES,
    NATIONAL_RECENT_RESULT_FIELDNAMES,
    TEAM_FIELDNAMES,
    TEAM_STRENGTH_SOURCE,
    TEAMS_MODULE_URL,
    build_team_rows,
    fetch_json,
    read_csv,
    read_json,
    write_csv,
    write_json,
)
from .world_cup_2026_odds import collect_world_cup_odds
from .world_cup_2026_player_form import (
    DEFAULT_MAX_PLAYERS_PER_TEAM,
    DEFAULT_PLAYER_PAGE_TIMEOUT_MS,
    DEFAULT_RECENT_CLUB_MATCH_LIMIT,
    build_team_player_target_counts,
    enrich_player_form,
)
from .world_cup_2026_recent_results import (
    DEFAULT_RECENT_MATCH_LIMIT,
    DEFAULT_SKILL_SCRIPTS_DIR,
    enrich_recent_results,
)
from .provider_router import (
    normalize_odds_provider,
    normalize_research_provider,
    resolve_provider_route,
)
from .sportradar_soccer import SportradarSoccerProvider


DEFAULT_TARGET_BUNDLE_DIR = ROOT / "data" / "research_import" / "targeted_backfill"
DEFAULT_DB_PATH = ROOT / "outputs" / "research_local.db"
DEFAULT_LOCAL_STORE_PATH = ROOT / "outputs" / "p0_local_store.json"
DEFAULT_LOCAL_DATE_UTC_OFFSET_HOURS = 8
DEFAULT_BOOKMAKER_ID = 549
DEFAULT_GAME_LINK_LIMIT = 200
DEFAULT_CRAWLER_STEP_TIMEOUT_SECONDS = 180.0
TARGETED_BACKFILL_SOURCE = "world_cup_targeted_research_backfill"
RECENT_RESULTS_IMPORT_SOURCE = "whoscored_recent_results_backfill"
SPORTRADAR_RECENT_RESULTS_SOURCE = "sportradar_soccer_recent_results_backfill"
SPORTRADAR_PLAYER_FORM_SOURCE = "sportradar_soccer_player_summaries_backfill"
HIGH_WEIGHT_COMPONENTS = {
    "team_strength",
    "recent_form",
    "attack_defense_efficiency",
    "lineup_integrity",
    "key_player_status",
}
HIGH_WEIGHT_MISSING_STATUSES = {"unavailable", "blocked", "neutral_default"}
BACKFILL_RESEARCH_PROVIDERS = {"crawler", "sportradar_soccer", "skip"}
BACKFILL_ODDS_PROVIDERS = {"crawler", "the_odds_api", "skip"}


def resolve_backfill_source_mode(
    requested: str | None,
    *,
    the_odds_api_key: str,
    skill_scripts_dir: Path,
) -> str:
    """Validate and retain the legacy CLI value without selecting providers."""

    del the_odds_api_key, skill_scripts_dir
    mode = str(requested or "auto").strip().lower()
    if mode not in {"auto", "api", "crawler"}:
        raise ValueError("source_mode_must_be_auto_api_or_crawler")
    return mode


def resolve_research_backfill_provider(
    source_mode: str,
    *,
    settings: Any,
    skill_scripts_dir: Path,
) -> str:
    return resolve_provider_route(
        settings=settings,
        skill_scripts_dir=skill_scripts_dir,
        legacy_source_mode=source_mode,
    ).research_provider


def resolve_odds_backfill_provider(
    source_mode: str,
    *,
    settings: Any,
    skill_scripts_dir: Path,
) -> str:
    return resolve_provider_route(
        settings=settings,
        skill_scripts_dir=skill_scripts_dir,
        legacy_source_mode=source_mode,
    ).odds_provider


def _normalize_research_provider(value: str) -> str:
    return normalize_research_provider(value)


def _normalize_odds_provider(value: str) -> str:
    return normalize_odds_provider(value)


def resolve_crawler_runtime_settings(
    *,
    requested_python_path: str | None,
    requested_timeout_seconds: float | None,
    settings: Any,
) -> tuple[str | None, float]:
    effective_python_path = requested_python_path or str(getattr(settings, "crawler_python_path", "") or "")
    effective_timeout_seconds = (
        requested_timeout_seconds
        if requested_timeout_seconds is not None
        else float(getattr(settings, "targeted_backfill_crawler_timeout_seconds", DEFAULT_CRAWLER_STEP_TIMEOUT_SECONDS))
    )
    return effective_python_path or None, effective_timeout_seconds


def resolve_target_fixture_rows(
    repository: ResearchDatabaseRepository,
    *,
    fixture_ids: list[str] | None = None,
    local_date: str | None = None,
    local_utc_offset_hours: int = DEFAULT_LOCAL_DATE_UTC_OFFSET_HOURS,
) -> list[dict[str, Any]]:
    if fixture_ids:
        rows = []
        for fixture_id in fixture_ids:
            fixture = repository.get_fixture(fixture_id)
            if fixture is None:
                raise ValueError(f"fixture_not_found:{fixture_id}")
            rows.append(fixture)
        return sorted(rows, key=lambda item: (str(item.get("match_time") or ""), str(item.get("fixture_id") or "")))

    if not local_date:
        raise ValueError("fixture_ids_or_local_date_required")

    target_date = datetime.fromisoformat(local_date).date()
    offset = timezone(timedelta(hours=local_utc_offset_hours))
    rows = []
    for fixture in repository.list_fixtures():
        if not is_world_cup_2026_fixture(fixture):
            continue
        match_time = _parse_datetime(str(fixture.get("match_time") or ""))
        if match_time is None:
            continue
        if match_time.astimezone(offset).date() == target_date:
            rows.append(fixture)
    return sorted(rows, key=lambda item: (str(item.get("match_time") or ""), str(item.get("fixture_id") or "")))


def default_available_at_for_fixtures(fixture_rows: list[dict[str, Any]]) -> str:
    cutoffs = []
    for fixture in fixture_rows:
        match_time = _parse_datetime(str(fixture.get("match_time") or ""))
        if match_time is None:
            continue
        cutoffs.append(match_time - timedelta(hours=3, minutes=1))
    if not cutoffs:
        raise ValueError("fixture_cutoff_unavailable")
    return min(cutoffs).isoformat()


def run_targeted_backfill(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    output_dir: Path = DEFAULT_TARGET_BUNDLE_DIR,
    fixture_ids: list[str] | None = None,
    local_date: str | None = None,
    available_at: str | None = None,
    local_utc_offset_hours: int = DEFAULT_LOCAL_DATE_UTC_OFFSET_HOURS,
    skill_scripts_dir: Path | None = None,
    recent_match_limit: int = DEFAULT_RECENT_MATCH_LIMIT,
    recent_club_match_limit: int = DEFAULT_RECENT_CLUB_MATCH_LIMIT,
    max_players_per_team: int = DEFAULT_MAX_PLAYERS_PER_TEAM,
    page_timeout_ms: int = DEFAULT_PLAYER_PAGE_TIMEOUT_MS,
    bookmaker_id: int = DEFAULT_BOOKMAKER_ID,
    game_link_limit: int = DEFAULT_GAME_LINK_LIMIT,
    source_mode: str | None = None,
    crawler_python_path: str | None = None,
    crawler_timeout_seconds: float | None = None,
    resume_existing: bool = True,
    local_store_repository: LocalRepository | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    skill_scripts_dir = skill_scripts_dir or Path(
        getattr(settings, "sports_stable_crawl_scripts_dir", DEFAULT_SKILL_SCRIPTS_DIR)
    )
    effective_source_mode = resolve_backfill_source_mode(
        source_mode,
        the_odds_api_key=settings.the_odds_api_key,
        skill_scripts_dir=skill_scripts_dir,
    )
    provider_route = resolve_provider_route(
        settings=settings,
        skill_scripts_dir=skill_scripts_dir,
        legacy_source_mode=effective_source_mode,
    )
    effective_research_provider = provider_route.research_provider
    effective_odds_provider = provider_route.odds_provider
    effective_crawler_python_path, effective_crawler_timeout_seconds = resolve_crawler_runtime_settings(
        requested_python_path=crawler_python_path,
        requested_timeout_seconds=crawler_timeout_seconds,
        settings=settings,
    )
    repository = ResearchDatabaseRepository(db_path)
    fixture_rows = resolve_target_fixture_rows(
        repository,
        fixture_ids=fixture_ids,
        local_date=local_date,
        local_utc_offset_hours=local_utc_offset_hours,
    )
    if not fixture_rows:
        raise ValueError("no_target_fixtures_resolved")

    target_team_rows = _target_team_rows_from_repository(repository, fixture_rows)
    target_team_ids = [row["team_id"] for row in target_team_rows]
    target_fixture_ids = [str(row["fixture_id"]) for row in fixture_rows]
    effective_available_at = available_at or default_available_at_for_fixtures(fixture_rows)
    recent_results = _pending_step_result("recent_results", selected_ids=target_team_ids)
    player_form = _pending_step_result("player_form", selected_ids=target_team_ids)
    odds = _pending_step_result("odds", selected_ids=target_fixture_ids)
    current_step = "prepare_target_bundle"
    try:
        _prepare_target_bundle(
            output_dir=output_dir,
            fixture_rows=fixture_rows,
            target_team_rows=target_team_rows,
            available_at=effective_available_at,
            preserve_existing=resume_existing,
            target_fixture_ids=set(target_fixture_ids),
            target_team_ids=set(target_team_ids),
        )

        recent_results_team_ids = (
            _missing_recent_results_team_ids(output_dir, target_team_ids)
            if resume_existing and effective_research_provider in {"crawler", "sportradar_soccer"}
            else target_team_ids if effective_research_provider in {"crawler", "sportradar_soccer"} else []
        )
        current_step = "recent_results"
        recent_results = (
            _collect_recent_results(
                output_dir=output_dir,
                accessed_at=effective_available_at,
                recent_match_limit=recent_match_limit,
                skill_scripts_dir=skill_scripts_dir,
                target_team_ids=recent_results_team_ids,
                crawler_python_path=effective_crawler_python_path,
                crawler_timeout_seconds=effective_crawler_timeout_seconds,
                preserve_existing=resume_existing,
                research_provider=effective_research_provider,
                settings=settings,
            )
            if recent_results_team_ids
            else _skipped_step_result(
                "recent_results",
                reason=(
                    "research_provider_unavailable"
                    if effective_research_provider == "skip"
                    else "existing_recent_results_cover_target_teams"
                ),
                selected_ids=target_team_ids,
            )
        )
        player_form_target_counts = _player_form_target_counts_for_bundle(
            output_dir,
            target_team_rows=target_team_rows,
            max_players_per_team=max_players_per_team,
        )
        player_form_team_ids = (
            _missing_player_form_team_ids(output_dir, player_form_target_counts)
            if resume_existing and effective_research_provider in {"crawler", "sportradar_soccer"}
            else target_team_ids if effective_research_provider in {"crawler", "sportradar_soccer"} else []
        )
        current_step = "player_form"
        player_form = (
            _collect_player_form(
                output_dir=output_dir,
                accessed_at=effective_available_at,
                recent_club_match_limit=recent_club_match_limit,
                max_players_per_team=max_players_per_team,
                page_timeout_ms=page_timeout_ms,
                skill_scripts_dir=skill_scripts_dir,
                target_team_ids=player_form_team_ids,
                crawler_python_path=effective_crawler_python_path,
                crawler_timeout_seconds=effective_crawler_timeout_seconds,
                preserve_existing=resume_existing,
                research_provider=effective_research_provider,
                settings=settings,
                team_target_counts=player_form_target_counts,
            )
            if player_form_team_ids
            else _skipped_step_result(
                "player_form",
                reason=(
                    "research_provider_unavailable"
                    if effective_research_provider == "skip"
                    else "existing_player_form_covers_target_teams"
                ),
                selected_ids=target_team_ids,
            )
        )
        odds_fixture_ids = (
            _missing_odds_fixture_ids(
                output_dir,
                target_fixture_ids,
                odds_provider=effective_odds_provider,
            )
            if resume_existing and effective_odds_provider in {"crawler", "the_odds_api"}
            else target_fixture_ids if effective_odds_provider in {"crawler", "the_odds_api"} else []
        )
        current_step = "odds"
        odds = (
            asyncio.run(
                collect_world_cup_odds(
                    output_dir=output_dir,
                    accessed_at=effective_available_at,
                    bookmaker_id=bookmaker_id,
                    game_link_limit=game_link_limit,
                    skill_scripts_dir=skill_scripts_dir,
                    only_fixture_ids=set(odds_fixture_ids),
                    preserve_existing=resume_existing,
                    source_mode="api" if effective_odds_provider == "the_odds_api" else "crawler",
                )
            )
            if odds_fixture_ids
            else _skipped_step_result(
                "odds",
                reason=(
                    "odds_provider_unavailable"
                    if effective_odds_provider == "skip"
                    else "existing_odds_cover_target_fixtures"
                ),
                selected_ids=target_fixture_ids,
            )
        )

        current_step = "import"
        payload = build_targeted_import_payload(
            output_dir,
            target_fixture_ids=set(target_fixture_ids),
            target_team_ids=set(target_team_ids),
        )
        upserted = repository.upsert_facts(payload)
        current_step = "readiness"
        readiness = build_targeted_readiness_report(
            repository,
            bundle_dir=output_dir,
            fixture_ids=target_fixture_ids,
            local_store_repository=local_store_repository or LocalRepository(JsonStore(DEFAULT_LOCAL_STORE_PATH)),
        )

        summary_data = _load_public_backfill_data(
            output_dir,
            target_fixture_ids=set(target_fixture_ids),
            target_team_ids=set(target_team_ids),
        )
        data_quality = _build_data_quality(
            recent_results=recent_results,
            player_form=player_form,
            odds=odds,
            data=summary_data,
        )
        summary = {
            "status": _overall_backfill_status(data_quality),
            "data_quality": data_quality,
            "data": summary_data,
            "source": {
                "research_provider": effective_research_provider,
                "odds_provider": effective_odds_provider,
                "route": provider_route.as_dict(),
            },
            "db_path": str(db_path),
            "bundle_dir": str(output_dir),
            "source_mode": effective_source_mode,
            "research_provider": effective_research_provider,
            "odds_provider": effective_odds_provider,
            "fixture_ids": target_fixture_ids,
            "team_ids": target_team_ids,
            "available_at": effective_available_at,
            "recent_results": recent_results,
            "player_form": player_form,
            "odds": odds,
            "imported": {
                "upserted": upserted,
                "payload_counts": {
                    key: len(value)
                    for key, value in payload.items()
                    if isinstance(value, list)
                },
            },
            "readiness": readiness,
        }
        write_json(output_dir / "targeted_backfill_summary.json", summary)
        return summary
    except Exception as exc:
        _write_failed_backfill_summary(
            output_dir=output_dir,
            db_path=db_path,
            source_mode=effective_source_mode,
            research_provider=effective_research_provider,
            odds_provider=effective_odds_provider,
            provider_route=provider_route.as_dict(),
            fixture_ids=target_fixture_ids,
            team_ids=target_team_ids,
            available_at=effective_available_at,
            failed_step=current_step,
            recent_results=recent_results,
            player_form=player_form,
            odds=odds,
            exc=exc,
        )
        raise


def _collect_recent_results(
    *,
    output_dir: Path,
    accessed_at: str,
    recent_match_limit: int,
    skill_scripts_dir: Path,
    target_team_ids: list[str],
    crawler_python_path: str | None,
    crawler_timeout_seconds: float,
    preserve_existing: bool,
    research_provider: str,
    settings: Any,
) -> dict[str, Any]:
    if research_provider == "sportradar_soccer":
        return _collect_recent_results_from_sportradar(
            output_dir=output_dir,
            accessed_at=accessed_at,
            recent_match_limit=recent_match_limit,
            target_team_ids=target_team_ids,
            preserve_existing=preserve_existing,
            settings=settings,
        )
    if crawler_python_path:
        return _run_json_module_in_crawler_python(
            module_name="app.research_db.world_cup_2026_recent_results",
            args=[
                "--output-dir", str(output_dir),
                "--accessed-at", accessed_at,
                "--recent-match-limit", str(recent_match_limit),
                "--skill-scripts-dir", str(skill_scripts_dir),
                *(("--replace-existing",) if not preserve_existing else ()),
                *[arg for team_id in target_team_ids for arg in ("--team-id", team_id)],
            ],
            crawler_python_path=crawler_python_path,
            timeout_seconds=crawler_timeout_seconds,
        )
    try:
        return asyncio.run(
            enrich_recent_results(
                output_dir=output_dir,
                accessed_at=accessed_at,
                recent_match_limit=recent_match_limit,
                selected_only=True,
                skill_scripts_dir=skill_scripts_dir,
                only_team_ids=set(target_team_ids),
                preserve_existing=preserve_existing,
            )
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(_missing_crawler_bridge_message(exc)) from exc


def _collect_player_form(
    *,
    output_dir: Path,
    accessed_at: str,
    recent_club_match_limit: int,
    max_players_per_team: int,
    page_timeout_ms: int,
    skill_scripts_dir: Path,
    target_team_ids: list[str],
    crawler_python_path: str | None,
    crawler_timeout_seconds: float,
    preserve_existing: bool,
    research_provider: str,
    settings: Any,
    team_target_counts: dict[str, int],
) -> dict[str, Any]:
    if research_provider == "sportradar_soccer":
        return _collect_player_form_from_sportradar(
            output_dir=output_dir,
            accessed_at=accessed_at,
            recent_club_match_limit=recent_club_match_limit,
            max_players_per_team=max_players_per_team,
            target_team_ids=target_team_ids,
            preserve_existing=preserve_existing,
            settings=settings,
            team_target_counts=team_target_counts,
        )
    if crawler_python_path:
        return _run_json_module_in_crawler_python(
            module_name="app.research_db.world_cup_2026_player_form",
            args=[
                "--output-dir", str(output_dir),
                "--accessed-at", accessed_at,
                "--recent-club-match-limit", str(recent_club_match_limit),
                "--max-players-per-team", str(max_players_per_team),
                "--page-timeout-ms", str(page_timeout_ms),
                "--skill-scripts-dir", str(skill_scripts_dir),
                *(("--replace-existing",) if not preserve_existing else ()),
                *[arg for team_id in target_team_ids for arg in ("--team-id", team_id)],
            ],
            crawler_python_path=crawler_python_path,
            timeout_seconds=crawler_timeout_seconds,
        )
    try:
        return asyncio.run(
            enrich_player_form(
                output_dir=output_dir,
                accessed_at=accessed_at,
                recent_club_match_limit=recent_club_match_limit,
                max_players_per_team=max_players_per_team,
                page_timeout_ms=page_timeout_ms,
                selected_only=True,
                skill_scripts_dir=skill_scripts_dir,
                only_team_ids=set(target_team_ids),
                preserve_existing=preserve_existing,
            )
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(_missing_crawler_bridge_message(exc)) from exc


def _run_json_module_in_crawler_python(
    *,
    module_name: str,
    args: list[str],
    crawler_python_path: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    command = [crawler_python_path, "-m", module_name, *args]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"crawler_python_step_timeout:{module_name}:{timeout_seconds}") from exc
    except OSError as exc:
        raise RuntimeError(f"crawler_python_step_start_failed:{module_name}:{exc}") from exc

    stdout_text = _decode_subprocess_stream(completed.stdout)
    stderr_text = _decode_subprocess_stream(completed.stderr)
    if completed.returncode != 0:
        detail = stderr_text.strip() or stdout_text.strip() or f"returncode={completed.returncode}"
        raise RuntimeError(f"crawler_python_step_failed:{module_name}:{detail}")
    try:
        payload = json.loads(stdout_text)
    except ValueError as exc:
        detail = stdout_text.strip() or stderr_text.strip() or "invalid_json"
        raise RuntimeError(f"crawler_python_step_invalid_json:{module_name}:{detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"crawler_python_step_invalid_payload:{module_name}")
    return payload


def _decode_subprocess_stream(value: bytes | str) -> str:
    if isinstance(value, str):
        return value
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _missing_crawler_bridge_message(exc: ModuleNotFoundError) -> str:
    if exc.name == "crawl4ai":
        return (
            "crawl4ai_not_available_in_app_runtime:"
            "set CRAWLER_PYTHON_PATH or point CRAWLER_COMMAND_PATH at the crawler Python runtime"
        )
    return str(exc)


def _collect_recent_results_from_sportradar(
    *,
    output_dir: Path,
    accessed_at: str,
    recent_match_limit: int,
    target_team_ids: list[str],
    preserve_existing: bool,
    settings: Any,
) -> dict[str, Any]:
    provider = SportradarSoccerProvider(settings)
    team_rows = read_csv(output_dir / "world_cup_2026_teams.csv")
    existing_rows = _load_existing_recent_result_rows_for_provider(
        output_dir,
        preserve_existing=preserve_existing,
        replacement_team_ids=set(target_team_ids),
    )
    known_rows = {str(row["team_id"]): row for row in team_rows}
    rows_by_source_team_id = {
        str(row.get("source_team_id") or ""): row
        for row in team_rows
        if str(row.get("source_team_id") or "")
    }
    diagnostics: list[dict[str, Any]] = []
    recent_result_rows = existing_rows[:]
    seen_fixture_ids = {
        str(row.get("source_fixture_id") or "")
        for row in recent_result_rows
        if str(row.get("source_fixture_id") or "")
    }
    for team_id in target_team_ids:
        team_row = known_rows.get(team_id)
        if team_row is None:
            diagnostics.append({"team_id": team_id, "status": "team_missing"})
            continue
        competitor_id = str(team_row.get("source_team_id") or "")
        response = provider.get_recent_results(competitor_id)
        if response.status != "ok":
            diagnostics.append({
                "team_id": team_id,
                "source_team_id": competitor_id,
                "status": str(response.diagnostics.get("error_code") or response.status),
                "message": str(response.diagnostics.get("message") or response.diagnostics.get("reason") or ""),
            })
            continue
        response_payload = response.data[0] if response.data else {}
        matches = _sportradar_recent_matches(
            response_payload,
            competitor_id=competitor_id,
            accessed_at=accessed_at,
            limit=recent_match_limit,
        )
        written = 0
        for match in matches:
            source_fixture_id = str(match["source_fixture_id"])
            if source_fixture_id in seen_fixture_ids:
                continue
            seen_fixture_ids.add(source_fixture_id)
            home_row = _ensure_sportradar_team_row(
                team_rows,
                rows_by_source_team_id,
                match["home_competitor"],
                accessed_at=accessed_at,
            )
            away_row = _ensure_sportradar_team_row(
                team_rows,
                rows_by_source_team_id,
                match["away_competitor"],
                accessed_at=accessed_at,
            )
            recent_result_rows.append({
                "fixture_id": f"recent_result_{_slug(source_fixture_id)}",
                "competition": match["competition"],
                "season": match["season"],
                "match_time": match["match_time"],
                "home_team_id": home_row["team_id"],
                "away_team_id": away_row["team_id"],
                "neutral_field": "false",
                "result_status": "closed",
                "home_score": str(match["home_score"]),
                "away_score": str(match["away_score"]),
                "opponent_team_id": away_row["team_id"] if home_row["team_id"] == team_id else home_row["team_id"],
                "source_fixture_id": source_fixture_id,
                "source_result_id": f"{source_fixture_id}:result",
                "available_at": accessed_at,
            })
            written += 1
        diagnostics.append({
            "team_id": team_id,
            "source_team_id": competitor_id,
            "status": "ok",
            "selected_matches": len(matches),
            "rows_written": written,
        })
    recent_result_rows.sort(key=lambda item: (str(item.get("match_time") or ""), str(item.get("fixture_id") or "")))
    team_rows.sort(key=lambda item: str(item.get("team_id") or ""))
    write_csv(output_dir / "world_cup_2026_teams.csv", TEAM_FIELDNAMES, team_rows)
    write_csv(output_dir / "national_recent_results.csv", NATIONAL_RECENT_RESULT_FIELDNAMES, recent_result_rows)
    payload = {
        "step": "recent_results",
        "status": "ok" if any(item.get("rows_written", 0) for item in diagnostics) else "partial",
        "source": "sportradar_soccer",
        "accessed_at": accessed_at,
        "selected_ids": target_team_ids,
        "recent_match_limit": recent_match_limit,
        "rows_written": len(recent_result_rows),
        "diagnostics": diagnostics,
    }
    write_json(output_dir / "recent_results_diagnostics.json", payload)
    return payload


def _collect_player_form_from_sportradar(
    *,
    output_dir: Path,
    accessed_at: str,
    recent_club_match_limit: int,
    max_players_per_team: int,
    target_team_ids: list[str],
    preserve_existing: bool,
    settings: Any,
    team_target_counts: dict[str, int],
) -> dict[str, Any]:
    provider = SportradarSoccerProvider(settings)
    team_rows = {str(row["team_id"]): row for row in read_csv(output_dir / "world_cup_2026_teams.csv")}
    core_player_rows = read_csv(output_dir / "core_players.csv")
    existing_snapshots = _load_existing_player_form_rows_for_provider(
        output_dir,
        preserve_existing=preserve_existing,
        replacement_team_ids=set(target_team_ids),
    )
    snapshots = existing_snapshots[:]
    diagnostics: list[dict[str, Any]] = []
    players_by_team: dict[str, list[dict[str, str]]] = {}
    for row in core_player_rows:
        players_by_team.setdefault(str(row["team_id"]), []).append(row)
    for items in players_by_team.values():
        items.sort(key=lambda item: int(item.get("selection_rank") or "999"))
    for team_id in target_team_ids:
        team_row = team_rows.get(team_id)
        if team_row is None:
            diagnostics.append({"team_id": team_id, "status": "team_missing"})
            continue
        competitor_id = str(team_row.get("source_team_id") or "")
        profile = provider.get_team_profile(competitor_id)
        if profile.status != "ok":
            diagnostics.append({
                "team_id": team_id,
                "source_team_id": competitor_id,
                "status": str(profile.diagnostics.get("error_code") or profile.status),
                "message": str(profile.diagnostics.get("message") or profile.diagnostics.get("reason") or ""),
            })
            continue
        profile_payload = profile.data[0] if profile.data else {}
        source_players = _sportradar_profile_players(profile_payload)
        source_players_by_name = {
            _normalize_person_name(str(item.get("name") or "")): item
            for item in source_players
            if _normalize_person_name(str(item.get("name") or ""))
        }
        selected_players = players_by_team.get(team_id, [])[:max(team_target_counts.get(team_id, max_players_per_team), 0)]
        built = 0
        for player_row in selected_players:
            source_player = source_players_by_name.get(_normalize_person_name(str(player_row.get("canonical_name") or "")))
            if source_player is None:
                snapshots.append(_empty_player_form_snapshot(player_row, accessed_at=accessed_at))
                continue
            response = provider.get_player_form(str(source_player.get("id") or ""))
            if response.status != "ok":
                snapshots.append(_empty_player_form_snapshot(player_row, accessed_at=accessed_at))
                continue
            response_payload = response.data[0] if response.data else {}
            snapshots.append(
                _build_sportradar_player_form_snapshot(
                    player_row,
                    summary_payload=response_payload,
                    competitor_id=competitor_id,
                    accessed_at=accessed_at,
                    recent_club_match_limit=recent_club_match_limit,
                    source_player_id=str(source_player.get("id") or ""),
                )
            )
            built += 1
        diagnostics.append({
            "team_id": team_id,
            "source_team_id": competitor_id,
            "status": "ok" if built == len(selected_players) else "partial",
            "selected_players": len(selected_players),
            "mapped_players": built,
        })
    snapshots.sort(key=lambda item: (str(item.get("team_id") or ""), str(item.get("player_id") or "")))
    write_json(output_dir / "player_form_snapshots.json", snapshots)
    overall_status = "ok"
    if any(str(item.get("status") or "") not in {"ok"} for item in diagnostics):
        overall_status = "partial"
    if diagnostics and all(str(item.get("status") or "") not in {"ok", "partial"} for item in diagnostics):
        overall_status = "error"
    payload = {
        "step": "player_form",
        "status": overall_status,
        "source": "sportradar_soccer",
        "accessed_at": accessed_at,
        "selected_ids": target_team_ids,
        "recent_club_match_limit": recent_club_match_limit,
        "rows_written": len(snapshots),
        "diagnostics": diagnostics,
    }
    write_json(output_dir / "player_form_diagnostics.json", payload)
    return payload


def _load_existing_recent_result_rows_for_provider(
    output_dir: Path,
    *,
    preserve_existing: bool,
    replacement_team_ids: set[str],
) -> list[dict[str, str]]:
    if not preserve_existing:
        return []
    path = output_dir / "national_recent_results.csv"
    if not path.exists():
        return []
    rows = read_csv(path)
    return [
        row
        for row in rows
        if str(row.get("home_team_id") or "") not in replacement_team_ids
        and str(row.get("away_team_id") or "") not in replacement_team_ids
        and str(row.get("opponent_team_id") or "") not in replacement_team_ids
    ]


def _load_existing_player_form_rows_for_provider(
    output_dir: Path,
    *,
    preserve_existing: bool,
    replacement_team_ids: set[str],
) -> list[dict[str, Any]]:
    if not preserve_existing:
        return []
    path = output_dir / "player_form_snapshots.json"
    if not path.exists():
        return []
    return [
        item
        for item in read_json(path)
        if isinstance(item, dict) and str(item.get("team_id") or "") not in replacement_team_ids
    ]


def _sportradar_recent_matches(
    payload: Any,
    *,
    competitor_id: str,
    accessed_at: str,
    limit: int,
) -> list[dict[str, Any]]:
    cutoff = _parse_datetime(accessed_at)
    items = payload.get("schedules") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        items = payload.get("sport_events") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    matches: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        event = item.get("sport_event") if isinstance(item.get("sport_event"), dict) else item
        status = item.get("sport_event_status") if isinstance(item.get("sport_event_status"), dict) else {}
        competitors = event.get("competitors") if isinstance(event.get("competitors"), list) else []
        if len(competitors) < 2:
            continue
        home = next((entry for entry in competitors if isinstance(entry, dict) and str(entry.get("qualifier") or "") == "home"), None)
        away = next((entry for entry in competitors if isinstance(entry, dict) and str(entry.get("qualifier") or "") == "away"), None)
        if home is None or away is None:
            continue
        competitor_ids = {str(home.get("id") or ""), str(away.get("id") or "")}
        if competitor_id not in competitor_ids:
            continue
        match_time = _parse_datetime(str(event.get("start_time") or ""))
        if match_time is None or (cutoff is not None and match_time >= cutoff):
            continue
        if str(status.get("status") or "").lower() not in {"closed", "ended", "finished"}:
            continue
        home_score = _int_or_none(status.get("home_score"))
        away_score = _int_or_none(status.get("away_score"))
        if home_score is None or away_score is None:
            continue
        competition = _sportradar_competition_name(event)
        matches.append({
            "source_fixture_id": str(event.get("id") or ""),
            "competition": competition,
            "season": str(match_time.year),
            "match_time": match_time.isoformat(),
            "home_score": home_score,
            "away_score": away_score,
            "home_competitor": home,
            "away_competitor": away,
        })
    matches.sort(key=lambda item: str(item["match_time"]), reverse=True)
    return matches[:limit]


def _ensure_sportradar_team_row(
    team_rows: list[dict[str, str]],
    rows_by_source_team_id: dict[str, dict[str, str]],
    competitor: dict[str, Any],
    *,
    accessed_at: str,
) -> dict[str, str]:
    source_team_id = str(competitor.get("id") or "")
    existing = rows_by_source_team_id.get(source_team_id)
    if existing is not None:
        return existing
    canonical_name = str(competitor.get("name") or source_team_id)
    country_code = str(competitor.get("country_code") or "")
    row = {
        "team_id": f"team_{_slug(source_team_id or canonical_name)}",
        "canonical_name": canonical_name,
        "country_code": country_code,
        "fifa_code": country_code,
        "source_team_id": source_team_id,
        "confederation": "",
        "stage": "",
        "host_team": "false",
        "available_at": accessed_at,
        "aliases": "|".join(_merge_aliases(canonical_name, country_code)),
    }
    team_rows.append(row)
    rows_by_source_team_id[source_team_id] = row
    return row


def _sportradar_competition_name(event: dict[str, Any]) -> str:
    context = event.get("sport_event_context") if isinstance(event.get("sport_event_context"), dict) else {}
    competition = context.get("competition") if isinstance(context.get("competition"), dict) else {}
    category = context.get("category") if isinstance(context.get("category"), dict) else {}
    return str(competition.get("name") or category.get("name") or "")


def _sportradar_profile_players(payload: Any) -> list[dict[str, Any]]:
    players = payload.get("players") if isinstance(payload, dict) else None
    if not isinstance(players, list):
        return []
    return [item for item in players if isinstance(item, dict) and item.get("id")]


def _empty_player_form_snapshot(player_row: dict[str, str], *, accessed_at: str) -> dict[str, Any]:
    return {
        "snapshot_id": f"player_form_{player_row['player_id']}",
        "player_id": player_row["player_id"],
        "team_id": player_row["team_id"],
        "club_name": player_row.get("club_name") or "",
        "club_source_id": player_row.get("club_source_id") or "",
        "as_of": accessed_at,
        "club_recent_matches": 0,
        "club_recent_starts": 0,
        "club_recent_minutes": 0,
        "club_recent_goals": None,
        "club_recent_assists": None,
        "national_recent_caps": 0,
        "national_recent_starts": 0,
        "national_recent_minutes": 0,
        "national_recent_goals": None,
        "national_recent_assists": None,
        "source": SPORTRADAR_PLAYER_FORM_SOURCE,
        "source_player_id": player_row.get("source_player_id") or player_row["player_id"],
        "available_at": accessed_at,
    }


def _build_sportradar_player_form_snapshot(
    player_row: dict[str, str],
    *,
    summary_payload: Any,
    competitor_id: str,
    accessed_at: str,
    recent_club_match_limit: int,
    source_player_id: str,
) -> dict[str, Any]:
    summaries = summary_payload.get("summaries") if isinstance(summary_payload, dict) else None
    if not isinstance(summaries, list):
        return _empty_player_form_snapshot(player_row, accessed_at=accessed_at)
    national_matches: list[dict[str, Any]] = []
    club_matches: list[dict[str, Any]] = []
    for item in summaries:
        if not isinstance(item, dict):
            continue
        competitors = _summary_competitors(item)
        if not competitors:
            continue
        target = national_matches if competitor_id in {str(entry.get("id") or "") for entry in competitors} else club_matches
        target.append(item)
    national_matches = _sort_summaries_desc(national_matches)[:5]
    club_matches = _sort_summaries_desc(club_matches)[:recent_club_match_limit]
    national_metrics = _summary_window_metrics(national_matches, source_player_id=source_player_id)
    club_metrics = _summary_window_metrics(club_matches, source_player_id=source_player_id)
    return {
        "snapshot_id": f"player_form_{player_row['player_id']}",
        "player_id": player_row["player_id"],
        "team_id": player_row["team_id"],
        "club_name": player_row.get("club_name") or "",
        "club_source_id": player_row.get("club_source_id") or "",
        "as_of": accessed_at,
        "club_recent_matches": len(club_matches),
        "club_recent_starts": club_metrics["starts"],
        "club_recent_minutes": club_metrics["minutes"],
        "club_recent_goals": club_metrics["goals"],
        "club_recent_assists": club_metrics["assists"],
        "national_recent_caps": len(national_matches),
        "national_recent_starts": national_metrics["starts"],
        "national_recent_minutes": national_metrics["minutes"],
        "national_recent_goals": national_metrics["goals"],
        "national_recent_assists": national_metrics["assists"],
        "source": SPORTRADAR_PLAYER_FORM_SOURCE,
        "source_player_id": source_player_id or player_row.get("source_player_id") or player_row["player_id"],
        "available_at": accessed_at,
    }


def _sort_summaries_desc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: str(_summary_match_time(item) or ""),
        reverse=True,
    )


def _summary_competitors(item: dict[str, Any]) -> list[dict[str, Any]]:
    event = item.get("sport_event") if isinstance(item.get("sport_event"), dict) else item
    competitors = event.get("competitors") if isinstance(event.get("competitors"), list) else []
    return [entry for entry in competitors if isinstance(entry, dict)]


def _summary_match_time(item: dict[str, Any]) -> str:
    event = item.get("sport_event") if isinstance(item.get("sport_event"), dict) else item
    return str(event.get("start_time") or "")


def _summary_window_metrics(items: list[dict[str, Any]], *, source_player_id: str) -> dict[str, int | None]:
    starts = 0
    minutes = 0
    goals = 0
    assists = 0
    explicit_minutes = False
    for item in items:
        stats = _extract_summary_player_stats(item, source_player_id=source_player_id)
        if stats["started"] is True:
            starts += 1
        elif stats["started"] is None and stats["appeared"]:
            starts += 1
        if stats["minutes"] is not None:
            minutes += stats["minutes"]
            explicit_minutes = True
        goals += stats["goals"] or 0
        assists += stats["assists"] or 0
    return {
        "starts": starts,
        "minutes": minutes if explicit_minutes else 0,
        "goals": goals or None,
        "assists": assists or None,
    }


def _extract_summary_player_stats(item: dict[str, Any], *, source_player_id: str) -> dict[str, Any]:
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    candidates: list[dict[str, Any]] = []
    totals = statistics.get("totals") if isinstance(statistics.get("totals"), dict) else {}
    competitors = totals.get("competitors") if isinstance(totals.get("competitors"), list) else []
    for competitor in competitors:
        if not isinstance(competitor, dict):
            continue
        players = competitor.get("players") if isinstance(competitor.get("players"), list) else []
        for player in players:
            if isinstance(player, dict):
                candidates.append(player)
    players = statistics.get("players") if isinstance(statistics.get("players"), list) else []
    candidates.extend(player for player in players if isinstance(player, dict))
    for player in candidates:
        player_ref = player.get("player") if isinstance(player.get("player"), dict) else player
        candidate_id = str(player_ref.get("id") or player.get("id") or "")
        if source_player_id and candidate_id and candidate_id != source_player_id:
            continue
        stats = player.get("statistics") if isinstance(player.get("statistics"), dict) else player
        minutes = _int_or_none(
            stats.get("minutes_played")
            or stats.get("minutes")
            or stats.get("played")
        )
        goals = _int_or_none(stats.get("goals_scored") or stats.get("goals"))
        assists = _int_or_none(stats.get("assists"))
        started_raw = stats.get("starter")
        started = None if started_raw is None else bool(started_raw)
        appeared = True
        return {
            "started": started,
            "minutes": minutes,
            "goals": goals,
            "assists": assists,
            "appeared": appeared,
        }
    return {"started": None, "minutes": None, "goals": None, "assists": None, "appeared": True}


def _normalize_person_name(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if "," in text:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) >= 2:
            text = " ".join(parts[1:] + parts[:1])
    ascii_value = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join("".join(char.lower() if char.isalnum() else " " for char in ascii_value).split())


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def build_targeted_import_payload(
    base_dir: Path,
    *,
    target_fixture_ids: set[str],
    target_team_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    team_rows = read_csv(base_dir / "world_cup_2026_teams.csv")
    core_player_rows = read_csv(base_dir / "core_players.csv")
    recent_results = read_csv(base_dir / "national_recent_results.csv")
    player_form_snapshots = [
        item for item in read_json(base_dir / "player_form_snapshots.json")
        if isinstance(item, dict)
    ]
    team_strength_snapshots = [
        item for item in read_json(base_dir / "team_strength_snapshots.json")
        if isinstance(item, dict)
    ]

    recent_result_team_ids = {
        str(row.get(field) or "")
        for row in recent_results
        for field in ("home_team_id", "away_team_id", "opponent_team_id")
        if str(row.get(field) or "")
    }
    support_teams = [
        row
        for row in team_rows
        if row["team_id"] in recent_result_team_ids and row["team_id"] not in target_team_ids
    ]

    payload: dict[str, list[dict[str, Any]]] = {
        "teams": [],
        "players": [],
        "fixtures": [],
        "match_results": [],
        "squads": [],
        "player_stats": [],
        "player_form_snapshots": [],
        "team_strength_snapshots": [],
        "team_aliases": [],
    }

    for row in support_teams:
        payload["teams"].append({
            "team_id": row["team_id"],
            "canonical_name": row["canonical_name"],
            "country_code": row.get("country_code") or "",
            "fifa_code": row.get("fifa_code") or "",
            "source": TARGETED_BACKFILL_SOURCE,
            "source_team_id": row.get("source_team_id") or row["team_id"],
            "available_at": row["available_at"],
        })

    for row in team_rows:
        source_team_id = str(row.get("source_team_id") or row["team_id"])
        for alias in _split_aliases(str(row.get("aliases") or "")):
            payload["team_aliases"].append({
                "alias_id": f"alias_{row['team_id']}_{_slug(alias)}",
                "entity_type": "team",
                "entity_id": row["team_id"],
                "alias": alias,
                "source": TARGETED_BACKFILL_SOURCE,
                "source_id": source_team_id,
                "confidence": 1.0,
                "available_at": row["available_at"],
            })

    seen_recent_fixtures = set()
    for row in recent_results:
        fixture_id = str(row["fixture_id"])
        if fixture_id in target_fixture_ids or fixture_id in seen_recent_fixtures:
            continue
        seen_recent_fixtures.add(fixture_id)
        payload["fixtures"].append({
            "fixture_id": fixture_id,
            "competition": row["competition"],
            "season": row["season"],
            "home_team_id": row["home_team_id"],
            "away_team_id": row["away_team_id"],
            "match_time": row["match_time"],
            "neutral_field": _bool_from_text(row.get("neutral_field")),
            "source": RECENT_RESULTS_IMPORT_SOURCE,
            "source_fixture_id": row["source_fixture_id"],
            "available_at": row["available_at"],
        })
        payload["match_results"].append({
            "result_id": row["source_result_id"],
            "fixture_id": fixture_id,
            "home_score": int(row["home_score"]),
            "away_score": int(row["away_score"]),
            "result_status": row["result_status"],
            "played_at": row["match_time"],
            "available_at": row["available_at"],
            "source": RECENT_RESULTS_IMPORT_SOURCE,
            "source_result_id": row["source_result_id"],
        })

    for row in core_player_rows:
        if row["team_id"] not in target_team_ids:
            continue
        payload["players"].append({
            "player_id": row["player_id"],
            "canonical_name": row["canonical_name"],
            "team_id": row["team_id"],
            "nationality": row["nationality"],
            "position": row["position"],
            "club": row.get("club_name") or None,
            "source": CORE_PLAYER_SOURCE,
            "source_player_id": row["source_player_id"],
            "available_at": row["available_at"],
        })
        payload["squads"].append({
            "squad_id": f"squad_{row['player_id']}_wc2026_core_shortlist",
            "team_id": row["team_id"],
            "competition": COMPETITION_NAME,
            "season": "2026",
            "player_id": row["player_id"],
            "role": row["squad_role"],
            "shirt_number": int(row["shirt_number"]) if str(row.get("shirt_number") or "").strip() else None,
            "source": CORE_PLAYER_SOURCE,
            "source_squad_id": f"{row['source_player_id']}:wc2026_core_shortlist",
            "available_at": row["available_at"],
        })

    payload["player_form_snapshots"] = [
        {
            "snapshot_id": row["snapshot_id"],
            "player_id": row["player_id"],
            "team_id": row["team_id"],
            "club_name": row.get("club_name"),
            "club_source_id": row.get("club_source_id"),
            "as_of": row["as_of"],
            "club_recent_matches": row.get("club_recent_matches"),
            "club_recent_starts": row.get("club_recent_starts"),
            "club_recent_minutes": row.get("club_recent_minutes"),
            "club_recent_goals": row.get("club_recent_goals"),
            "club_recent_assists": row.get("club_recent_assists"),
            "national_recent_caps": row.get("national_recent_caps"),
            "national_recent_starts": row.get("national_recent_starts"),
            "national_recent_minutes": row.get("national_recent_minutes"),
            "national_recent_goals": row.get("national_recent_goals"),
            "national_recent_assists": row.get("national_recent_assists"),
            "source": row.get("source") or TARGETED_BACKFILL_SOURCE,
            "source_player_id": row["source_player_id"],
            "available_at": row["available_at"],
        }
        for row in player_form_snapshots
        if str(row.get("team_id") or "") in target_team_ids
    ]
    payload["team_strength_snapshots"] = [
        {
            "snapshot_id": row["snapshot_id"],
            "team_id": row["team_id"],
            "strength_type": row["strength_type"],
            "strength_value": row["strength_value"],
            "strength_source": row["strength_source"],
            "source": row.get("source") or row["strength_source"],
            "source_team_id": row["source_team_id"],
            "as_of": row["as_of"],
            "available_at": row["available_at"],
        }
        for row in team_strength_snapshots
        if str(row.get("team_id") or "") in target_team_ids
    ]
    return payload


def build_targeted_readiness_report(
    repository: ResearchDatabaseRepository,
    *,
    bundle_dir: Path,
    fixture_ids: list[str],
    local_store_repository: LocalRepository | None = None,
) -> list[dict[str, Any]]:
    local_store_repository = local_store_repository or LocalRepository(JsonStore(DEFAULT_LOCAL_STORE_PATH))
    service = PreMatchResearchScoringService(
        repository,
        bundle_dir=bundle_dir,
        local_store_repository=local_store_repository,
    )
    readiness_rows = []
    for fixture_id in fixture_ids:
        fixture = repository.get_fixture(fixture_id)
        if fixture is None:
            readiness_rows.append({
                "fixture_id": fixture_id,
                "status": "failed",
                "reason": "fixture_not_found",
            })
            continue
        feature_vector, prediction = service.build_prediction_with_feature_vector(fixture_id)
        save_prediction = getattr(service, "save_prediction", None)
        if callable(save_prediction):
            prediction = save_prediction(prediction)
        readiness = _public_data_readiness(
            match_id=fixture_id,
            fixture=fixture,
            research_repository=repository,
            feature_vector=feature_vector,
            prediction=prediction,
        )
        readiness_rows.append({
            "fixture_id": fixture_id,
            "ready": readiness["ready"],
            "blocked_reason": readiness["blocked_reason"],
            "high_weight_missing_components": readiness["high_weight_missing_components"],
            "research_fact_counts": readiness["research_fact_counts"],
            "odds_status": readiness["odds"]["status"],
            "component_coverage": readiness["component_coverage"],
        })
    return readiness_rows


def _public_data_readiness(
    *,
    match_id: str,
    fixture: dict[str, Any],
    research_repository: ResearchDatabaseRepository,
    feature_vector: dict[str, Any],
    prediction: dict[str, Any],
) -> dict[str, Any]:
    component_coverage = {
        str(component.get("dimension")): {
            "status": str(component.get("status") or "unknown"),
            "quality_score": float(component.get("quality_score") or 0.0),
            "missing_reason": component.get("missing_reason"),
        }
        for component in prediction.get("components", [])
        if isinstance(component, dict) and str(component.get("dimension") or "").strip()
    }
    high_weight_missing = sorted(
        {
            dimension
            for dimension, component in component_coverage.items()
            if dimension in HIGH_WEIGHT_COMPONENTS
            and str(component.get("status") or "unknown") in HIGH_WEIGHT_MISSING_STATUSES
        }
    )
    available_at_cutoff = str(feature_vector.get("as_of") or fixture.get("match_time") or "")
    fact_counts = _research_fact_counts_for_fixture(
        research_repository,
        fixture=fixture,
        available_at_cutoff=available_at_cutoff,
    )
    blocked_reason_codes: list[str] = []
    if fact_counts["team_strength_snapshots"] == 0:
        blocked_reason_codes.append("team_strength_missing")
    if fact_counts["match_results"] == 0:
        blocked_reason_codes.extend(["recent_form_missing", "attack_defense_efficiency_missing"])
    if fact_counts["player_form_snapshots"] == 0:
        blocked_reason_codes.append("player_form_missing")
    for dimension in high_weight_missing:
        blocked_reason_codes.append(f"{dimension}_missing")
    odds_status = str(feature_vector.get("odds", {}).get("status") or "unavailable")
    if odds_status in HIGH_WEIGHT_MISSING_STATUSES:
        blocked_reason_codes.append("odds_movement_missing")
    blocked_reason_codes = list(dict.fromkeys(blocked_reason_codes))
    return {
        "ready": not blocked_reason_codes,
        "blocked_reason": ";".join(blocked_reason_codes) if blocked_reason_codes else None,
        "component_coverage": component_coverage,
        "high_weight_missing_components": high_weight_missing,
        "research_fact_counts": fact_counts,
        "odds": {"status": odds_status},
        "match_id": match_id,
    }


def _research_fact_counts_for_fixture(
    repository: ResearchDatabaseRepository,
    *,
    fixture: dict[str, Any],
    available_at_cutoff: str,
) -> dict[str, int]:
    team_ids = {
        str(fixture.get("home_team_id") or ""),
        str(fixture.get("away_team_id") or ""),
    }
    team_ids.discard("")
    match_time = str(fixture.get("match_time") or "")
    match_results = 0
    team_strength_snapshots = 0
    player_form_snapshots = 0
    for team_id in team_ids:
        match_results += len(
            repository.recent_results_for_team(
                team_id,
                match_time=match_time,
                available_at_cutoff=available_at_cutoff,
                limit=5,
            )
        )
        team_strength_snapshots += sum(
            1
            for row in repository.list_team_strength_snapshots(team_id=team_id)
            if str(row.get("available_at") or row.get("as_of") or "") <= available_at_cutoff
        )
        player_form_snapshots += sum(
            1
            for row in repository.list_player_form_snapshots(team_id=team_id)
            if str(row.get("available_at") or row.get("as_of") or "") <= available_at_cutoff
        )
    return {
        "match_results": match_results,
        "team_strength_snapshots": team_strength_snapshots,
        "player_form_snapshots": player_form_snapshots,
    }


def _prepare_target_bundle(
    *,
    output_dir: Path,
    fixture_rows: list[dict[str, Any]],
    target_team_rows: list[dict[str, Any]],
    available_at: str,
    preserve_existing: bool,
    target_fixture_ids: set[str],
    target_team_ids: set[str],
) -> None:
    if preserve_existing:
        bundle_state = _existing_bundle_state(
            output_dir,
            target_fixture_ids=target_fixture_ids,
            target_team_ids=target_team_ids,
        )
        if bundle_state == "ready":
            return
        if bundle_state == "scope_mismatch":
            raise ValueError("existing_bundle_scope_mismatch")
        if bundle_state == "incomplete":
            raise ValueError("existing_bundle_incomplete")
    output_dir.mkdir(parents=True, exist_ok=True)
    official_team_rows, official_strength_rows = _load_official_team_seed_rows(available_at)
    staging_team_rows, team_strength_rows = _build_staging_team_rows(
        target_team_rows=target_team_rows,
        official_team_rows=official_team_rows,
        official_strength_rows=official_strength_rows,
        available_at=available_at,
    )
    staging_fixture_rows = _build_staging_fixture_rows(
        fixture_rows=fixture_rows,
        staging_team_rows=staging_team_rows,
        available_at=available_at,
    )
    core_player_rows = _build_staging_core_player_rows(
        target_team_rows=target_team_rows,
        staging_team_rows=staging_team_rows,
        available_at=available_at,
    )
    manifest = _build_target_manifest(
        team_rows=staging_team_rows,
        fixture_rows=staging_fixture_rows,
        core_player_rows=core_player_rows,
        available_at=available_at,
    )

    write_csv(output_dir / "world_cup_2026_teams.csv", TEAM_FIELDNAMES, staging_team_rows)
    write_csv(output_dir / "world_cup_2026_fixtures.csv", FIXTURE_FIELDNAMES, staging_fixture_rows)
    write_csv(output_dir / "core_players.csv", CORE_PLAYER_FIELDNAMES, core_player_rows)
    write_csv(output_dir / "national_recent_results.csv", NATIONAL_RECENT_RESULT_FIELDNAMES, [])
    write_json(output_dir / "player_form_snapshots.json", [])
    write_json(output_dir / "odds_snapshots.json", [])
    write_json(output_dir / "team_strength_snapshots.json", team_strength_rows)
    write_json(output_dir / "source_manifest.json", manifest)


def _target_team_rows_from_repository(
    repository: ResearchDatabaseRepository,
    fixture_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    team_ids = []
    seen = set()
    for fixture in fixture_rows:
        for field in ("home_team_id", "away_team_id"):
            team_id = str(fixture.get(field) or "")
            if team_id and team_id not in seen:
                seen.add(team_id)
                team_ids.append(team_id)
    rows = []
    for team_id in team_ids:
        team = repository.get_team(team_id)
        if team is None:
            raise ValueError(f"team_not_found:{team_id}")
        rows.append(team)
    return rows


def _build_staging_team_rows(
    *,
    target_team_rows: list[dict[str, Any]],
    official_team_rows: list[dict[str, str]],
    official_strength_rows: list[dict[str, Any]],
    available_at: str,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    official_by_key = {
        key: row
        for row in official_team_rows
        for key in _team_keys(row.get("canonical_name"), row.get("aliases"))
    }
    strength_by_official_team_id = {
        str(row["team_id"]): row
        for row in official_strength_rows
    }
    staging_team_rows: list[dict[str, str]] = []
    team_strength_rows: list[dict[str, Any]] = []
    for target_team in target_team_rows:
        official_team = _match_official_team_row(target_team, official_by_key)
        if official_team is None:
            raise ValueError(f"official_team_seed_missing:{target_team['canonical_name']}")
        aliases = _merge_aliases(
            target_team.get("canonical_name"),
            target_team.get("fifa_code"),
            official_team.get("aliases"),
        )
        staging_team_rows.append({
            "team_id": str(target_team["team_id"]),
            "canonical_name": str(target_team["canonical_name"]),
            "country_code": str(official_team.get("country_code") or target_team.get("country_code") or ""),
            "fifa_code": str(official_team.get("fifa_code") or target_team.get("fifa_code") or ""),
            "source_team_id": str(target_team.get("source_team_id") or target_team["team_id"]),
            "confederation": str(official_team.get("confederation") or ""),
            "stage": str(official_team.get("stage") or ""),
            "host_team": str(official_team.get("host_team") or "false"),
            "available_at": available_at,
            "aliases": "|".join(aliases),
        })
        official_strength = strength_by_official_team_id.get(str(official_team["team_id"]))
        if official_strength is None:
            raise ValueError(f"official_team_strength_missing:{target_team['canonical_name']}")
        team_strength_rows.append({
            "snapshot_id": f"team_strength_{target_team['team_id']}_{available_at[:10]}_fifa_world_ranking_position",
            "team_id": str(target_team["team_id"]),
            "strength_type": str(official_strength["strength_type"]),
            "strength_value": float(official_strength["strength_value"]),
            "strength_source": str(official_strength["strength_source"]),
            "source": str(official_strength.get("source") or TEAM_STRENGTH_SOURCE),
            "source_team_id": str(official_strength["source_team_id"]),
            "as_of": available_at,
            "available_at": available_at,
        })
    staging_team_rows.sort(key=lambda item: item["team_id"])
    team_strength_rows.sort(key=lambda item: item["team_id"])
    return staging_team_rows, team_strength_rows


def _build_staging_fixture_rows(
    *,
    fixture_rows: list[dict[str, Any]],
    staging_team_rows: list[dict[str, str]],
    available_at: str,
) -> list[dict[str, str]]:
    team_by_id = {row["team_id"]: row for row in staging_team_rows}
    rows = []
    for index, fixture in enumerate(fixture_rows, start=1):
        home_team = team_by_id.get(str(fixture["home_team_id"]), {})
        away_team = team_by_id.get(str(fixture["away_team_id"]), {})
        rows.append({
            "match_no": str(index),
            "fixture_id": str(fixture["fixture_id"]),
            "match_time": str(fixture["match_time"]),
            "local_match_time": str(fixture["match_time"]),
            "home_team_id": str(fixture["home_team_id"]),
            "away_team_id": str(fixture["away_team_id"]),
            "home_fifa_code": str(home_team.get("fifa_code") or ""),
            "away_fifa_code": str(away_team.get("fifa_code") or ""),
            "result_status": "scheduled",
            "competition": str(fixture.get("competition") or COMPETITION_NAME),
            "season": str(fixture.get("season") or "2026"),
            "stage": "",
            "group_name": "",
            "neutral_field": "true" if bool(fixture.get("neutral_field")) else "false",
            "stadium": "",
            "city": "",
            "fixture_available_at": available_at,
            "source_fixture_id": str(fixture.get("source_fixture_id") or fixture["fixture_id"]),
        })
    return rows


def _build_staging_core_player_rows(
    *,
    target_team_rows: list[dict[str, Any]],
    staging_team_rows: list[dict[str, str]],
    available_at: str,
) -> list[dict[str, str]]:
    seed_team_rows = read_csv(ROOT / "data" / "research_import" / "p0_11" / "world_cup_2026_teams.csv")
    seed_core_players = read_csv(ROOT / "data" / "research_import" / "p0_11" / "core_players.csv")
    seed_by_team_id = {row["team_id"]: row for row in seed_team_rows}
    seed_team_id_by_key = {
        key: row["team_id"]
        for row in seed_team_rows
        for key in _team_keys(row.get("canonical_name"), row.get("aliases"))
    }
    target_seed_team_ids = {
        str(target_team["team_id"]): seed_team_id_by_key.get(_normalize_key(str(target_team["canonical_name"])))
        for target_team in target_team_rows
    }
    staging_team_by_id = {row["team_id"]: row for row in staging_team_rows}
    rows = []
    for target_team_id, seed_team_id in target_seed_team_ids.items():
        if not seed_team_id or seed_team_id not in seed_by_team_id:
            raise ValueError(f"core_player_seed_missing:{target_team_id}")
        staging_team = staging_team_by_id[target_team_id]
        for row in seed_core_players:
            if row["team_id"] != seed_team_id:
                continue
            rows.append({
                "player_id": row["player_id"],
                "canonical_name": row["canonical_name"],
                "team_id": target_team_id,
                "nationality": str(staging_team.get("country_code") or row.get("nationality") or ""),
                "position": row["position"],
                "club_name": row.get("club_name") or "",
                "club_source_id": row.get("club_source_id") or "",
                "source_player_id": row["source_player_id"],
                "fifa_player_id": row.get("fifa_player_id") or "",
                "squad_role": row["squad_role"],
                "shirt_number": row.get("shirt_number") or "",
                "selection_rank": row["selection_rank"],
                "selection_reason": row["selection_reason"],
                "available_at": available_at,
            })
    rows.sort(key=lambda item: (item["team_id"], int(item["selection_rank"] or "999")))
    return rows


def _build_target_manifest(
    *,
    team_rows: list[dict[str, str]],
    fixture_rows: list[dict[str, str]],
    core_player_rows: list[dict[str, str]],
    available_at: str,
) -> dict[str, Any]:
    return {
        "dataset_id": "wc2026_targeted_research_backfill",
        "objective": "Targeted prematch backfill for selected World Cup fixtures using SR team ids.",
        "source": TARGETED_BACKFILL_SOURCE,
        "competition": COMPETITION_NAME,
        "season": "2026",
        "coverage_mode": "targeted_fixture_backfill",
        "selected_team_ids": [row["team_id"] for row in team_rows],
        "default_snapshot_fixture_ids": [row["fixture_id"] for row in fixture_rows],
        "teams_file": "world_cup_2026_teams.csv",
        "fixtures_file": "world_cup_2026_fixtures.csv",
        "matches_file": "national_recent_results.csv",
        "players_file": "core_players.csv",
        "player_form_file": "player_form_snapshots.json",
        "odds_file": "odds_snapshots.json",
        "odds_diagnostics_file": "odds_diagnostics.json",
        "team_strength_file": "team_strength_snapshots.json",
        "accessed_at": available_at,
        "source_policy": {
            "strategy": "targeted_sr_team_id_backfill_with_supported_structured_sources",
            "raw_saved": False,
            "network_required_for_import": False,
            "notes": [
                "Bundle is seeded for selected fixtures only.",
                "Recent results and player form use the validated WhoScored path.",
                "Team strength uses FIFA ranking snapshots mapped onto existing SR team ids.",
            ],
        },
        "source_urls": [
            TEAMS_MODULE_URL,
        ],
        "core_player_shortlist_size": max(
            (sum(1 for row in core_player_rows if row["team_id"] == team_id) for team_id in {row["team_id"] for row in team_rows}),
            default=0,
        ),
        "schema_gaps": {
            "national_recent_results_pending": True,
            "player_form_snapshots_pending": True,
            "odds_snapshots_pending": True,
            "club_mapping_pending": True,
        },
        "coverage_summary": {
            "teams": len(team_rows),
            "fixtures": len(fixture_rows),
            "core_players": len(core_player_rows),
            "odds_snapshots": 0,
        },
    }


def _existing_bundle_state(
    output_dir: Path,
    *,
    target_fixture_ids: set[str],
    target_team_ids: set[str],
) -> str:
    manifest_path = output_dir / "source_manifest.json"
    if not manifest_path.exists():
        return "missing"
    required_paths = (
        output_dir / "world_cup_2026_teams.csv",
        output_dir / "world_cup_2026_fixtures.csv",
        output_dir / "core_players.csv",
        output_dir / "team_strength_snapshots.json",
        manifest_path,
    )
    if not all(path.exists() for path in required_paths):
        return "incomplete"
    manifest = read_json(manifest_path)
    existing_fixture_ids = {str(item) for item in manifest.get("default_snapshot_fixture_ids", [])}
    existing_team_ids = {str(item) for item in manifest.get("selected_team_ids", [])}
    if existing_fixture_ids == target_fixture_ids and existing_team_ids == target_team_ids:
        return "ready"
    return "scope_mismatch"


def _missing_recent_results_team_ids(output_dir: Path, target_team_ids: list[str]) -> list[str]:
    path = output_dir / "national_recent_results.csv"
    if not path.exists():
        return target_team_ids[:]
    rows = read_csv(path)
    covered = {
        str(row.get(field) or "")
        for row in rows
        for field in ("home_team_id", "away_team_id")
        if str(row.get(field) or "")
    }
    return [team_id for team_id in target_team_ids if team_id not in covered]


def _player_form_target_counts_for_bundle(
    output_dir: Path,
    *,
    target_team_rows: list[dict[str, Any]],
    max_players_per_team: int,
) -> dict[str, int]:
    manifest_path = output_dir / "source_manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        payload = manifest.get("player_form_target_counts") if isinstance(manifest, dict) else None
        if isinstance(payload, dict):
            counts: dict[str, int] = {}
            for team_id, value in payload.items():
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    counts[str(team_id)] = parsed
            if counts:
                return counts
    strength_path = output_dir / "team_strength_snapshots.json"
    strength_rows = [
        item
        for item in read_json(strength_path)
        if isinstance(item, dict)
    ] if strength_path.exists() else []
    team_rows = [
        {
            "team_id": str(row["team_id"]),
            "canonical_name": str(row.get("canonical_name") or ""),
        }
        for row in target_team_rows
    ]
    return build_team_player_target_counts(
        team_rows,
        strength_rows,
        max_players_per_team=max_players_per_team,
    )


def _missing_player_form_team_ids(
    output_dir: Path,
    team_target_counts: dict[str, int],
) -> list[str]:
    path = output_dir / "player_form_snapshots.json"
    if not path.exists():
        return list(team_target_counts)
    snapshots = [
        item
        for item in read_json(path)
        if isinstance(item, dict)
    ]
    counts: dict[str, int] = {}
    for snapshot in snapshots:
        team_id = str(snapshot.get("team_id") or "")
        if not team_id:
            continue
        counts[team_id] = counts.get(team_id, 0) + 1
    return [
        team_id
        for team_id, expected in team_target_counts.items()
        if counts.get(team_id, 0) < expected
    ]


def _missing_odds_fixture_ids(
    output_dir: Path,
    target_fixture_ids: list[str],
    *,
    odds_provider: str = "crawler",
) -> list[str]:
    path = output_dir / "odds_snapshots.json"
    if not path.exists():
        return target_fixture_ids[:]
    rows = [
        item
        for item in read_json(path)
        if isinstance(item, dict)
    ]
    markets_by_fixture: dict[str, set[str]] = {}
    for row in rows:
        fixture_id = str(row.get("fixture_id") or "")
        market_type = str(row.get("market_type") or "")
        if not fixture_id or not market_type:
            continue
        markets_by_fixture.setdefault(fixture_id, set()).add(market_type)
    if odds_provider == "the_odds_api":
        return [
            fixture_id
            for fixture_id in target_fixture_ids
            if not markets_by_fixture.get(fixture_id, set())
        ]
    required_markets = {"h2h", "spreads", "totals"}
    return [
        fixture_id
        for fixture_id in target_fixture_ids
        if markets_by_fixture.get(fixture_id, set()) != required_markets
    ]


def _load_public_backfill_data(
    output_dir: Path,
    *,
    target_fixture_ids: set[str],
    target_team_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    recent_results = [
        row
        for row in _safe_csv_rows(output_dir / "national_recent_results.csv")
        if target_team_ids.intersection(
            {
                str(row.get("home_team_id") or ""),
                str(row.get("away_team_id") or ""),
                str(row.get("opponent_team_id") or ""),
            }
        )
    ]
    player_form = [
        row
        for row in _safe_json_rows(output_dir / "player_form_snapshots.json")
        if str(row.get("team_id") or "") in target_team_ids
    ]
    odds = [
        row
        for row in _safe_json_rows(output_dir / "odds_snapshots.json")
        if str(row.get("fixture_id") or "") in target_fixture_ids
    ]
    return {
        "recent_results": recent_results,
        "player_form": player_form,
        "odds": odds,
    }


def _safe_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return [dict(row) for row in read_csv(path)]
    except (OSError, TypeError, ValueError):
        return []


def _safe_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = read_json(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [dict(row) for row in payload if isinstance(row, dict)]


def _build_data_quality(
    *,
    recent_results: dict[str, Any],
    player_form: dict[str, Any],
    odds: dict[str, Any],
    data: dict[str, list[dict[str, Any]]],
) -> dict[str, str]:
    return {
        "recent_results": _step_data_quality(
            recent_results,
            rows=data["recent_results"],
        ),
        "player_form": _step_data_quality(
            player_form,
            rows=data["player_form"],
        ),
        "odds": _step_data_quality(
            odds,
            rows=data["odds"],
        ),
    }


def _step_data_quality(
    step_result: dict[str, Any],
    *,
    rows: list[dict[str, Any]],
) -> str:
    if not rows:
        return "missing"
    status = str(step_result.get("status") or "").strip().lower()
    if status in {"partial", "error", "failed"}:
        return "partial"
    if status in {"ok", "saved"}:
        return "ok"
    if status == "skipped":
        reason = str(step_result.get("reason") or "")
        return "ok" if reason.startswith("existing_") else "partial"
    diagnostics = step_result.get("diagnostics")
    if isinstance(diagnostics, list) and diagnostics:
        diagnostic_statuses = {
            str(item.get("status") or "").strip().lower()
            for item in diagnostics
            if isinstance(item, dict)
        }
        success_statuses = {"ok", "saved"}
        return "ok" if diagnostic_statuses and diagnostic_statuses <= success_statuses else "partial"
    return "ok"


def _overall_backfill_status(data_quality: dict[str, str]) -> str:
    return "ok" if data_quality and set(data_quality.values()) == {"ok"} else "partial"


def _skipped_step_result(step: str, *, reason: str, selected_ids: list[str]) -> dict[str, Any]:
    return {
        "status": "skipped",
        "step": step,
        "reason": reason,
        "selected_ids": selected_ids,
    }


def _pending_step_result(step: str, *, selected_ids: list[str]) -> dict[str, Any]:
    return {
        "status": "pending",
        "step": step,
        "selected_ids": selected_ids,
    }


def _write_failed_backfill_summary(
    *,
    output_dir: Path,
    db_path: Path,
    source_mode: str,
    research_provider: str,
    odds_provider: str,
    provider_route: dict[str, Any],
    fixture_ids: list[str],
    team_ids: list[str],
    available_at: str,
    failed_step: str,
    recent_results: dict[str, Any],
    player_form: dict[str, Any],
    odds: dict[str, Any],
    exc: Exception,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_data = _load_public_backfill_data(
        output_dir,
        target_fixture_ids=set(fixture_ids),
        target_team_ids=set(team_ids),
    )
    data_quality = _build_data_quality(
        recent_results=recent_results,
        player_form=player_form,
        odds=odds,
        data=summary_data,
    )
    write_json(
        output_dir / "targeted_backfill_summary.json",
        {
            "status": "failed",
            "data_quality": data_quality,
            "data": summary_data,
            "source": {
                "research_provider": research_provider,
                "odds_provider": odds_provider,
                "route": provider_route,
            },
            "db_path": str(db_path),
            "bundle_dir": str(output_dir),
            "source_mode": source_mode,
            "research_provider": research_provider,
            "odds_provider": odds_provider,
            "fixture_ids": fixture_ids,
            "team_ids": team_ids,
            "available_at": available_at,
            "failed_step": failed_step,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc).strip(),
            },
            "recent_results": recent_results,
            "player_form": player_form,
            "odds": odds,
        },
    )


def _load_official_team_seed_rows(available_at: str) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    seed_dir = ROOT / "data" / "research_import" / "p0_11"
    fallback_strength_rows = [
        item for item in read_json(seed_dir / "team_strength_snapshots.json")
        if isinstance(item, dict)
    ]
    try:
        teams_module = fetch_json(TEAMS_MODULE_URL)
        team_rows, strength_rows = build_team_rows(teams_module, accessed_at=available_at)
        return team_rows, strength_rows or fallback_strength_rows
    except Exception:  # noqa: BLE001
        return (
            read_csv(seed_dir / "world_cup_2026_teams.csv"),
            fallback_strength_rows,
        )


def _match_official_team_row(
    target_team: dict[str, Any],
    official_by_key: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    for key in _team_keys(target_team.get("canonical_name"), target_team.get("fifa_code")):
        row = official_by_key.get(key)
        if row is not None:
            return row
    return None


def _merge_aliases(*values: Any) -> list[str]:
    ordered = []
    seen = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            parts = value.split("|")
        else:
            parts = [str(value)]
        for part in parts:
            cleaned = str(part or "").strip()
            if not cleaned:
                continue
            key = _normalize_key(cleaned)
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(cleaned)
    return ordered


def _team_keys(*values: Any) -> set[str]:
    keys = set()
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            parts = value.split("|")
        else:
            parts = [str(value)]
        for part in parts:
            key = _normalize_key(part)
            if key:
                keys.add(key)
    return keys


def _split_aliases(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def _normalize_key(value: Any) -> str:
    ascii_value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join("".join(char.lower() if char.isalnum() else " " for char in ascii_value).split())


def _slug(value: str) -> str:
    return "_".join(part for part in "".join(char.lower() if char.isalnum() else "_" for char in value).split("_") if part)


def _bool_from_text(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _emit_json(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(__import__("sys").stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        return
    print(text, end="")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_TARGET_BUNDLE_DIR)
    parser.add_argument("--fixture-id", action="append", default=[])
    parser.add_argument("--local-date")
    parser.add_argument("--available-at")
    parser.add_argument("--local-utc-offset-hours", type=int, default=DEFAULT_LOCAL_DATE_UTC_OFFSET_HOURS)
    parser.add_argument(
        "--skill-scripts-dir",
        type=Path,
        default=None,
        help="Crawler scripts directory; defaults to SPORTS_STABLE_CRAWL_SCRIPTS_DIR.",
    )
    parser.add_argument("--recent-match-limit", type=int, default=DEFAULT_RECENT_MATCH_LIMIT)
    parser.add_argument("--recent-club-match-limit", type=int, default=DEFAULT_RECENT_CLUB_MATCH_LIMIT)
    parser.add_argument("--max-players-per-team", type=int, default=DEFAULT_MAX_PLAYERS_PER_TEAM)
    parser.add_argument("--page-timeout-ms", type=int, default=DEFAULT_PLAYER_PAGE_TIMEOUT_MS)
    parser.add_argument("--bookmaker-id", type=int, default=DEFAULT_BOOKMAKER_ID)
    parser.add_argument("--game-link-limit", type=int, default=DEFAULT_GAME_LINK_LIMIT)
    parser.add_argument(
        "--source-mode",
        choices=("auto", "api", "crawler"),
        default="auto",
        help="Deprecated compatibility metadata; provider selection comes from DEFAULT_*_PROVIDER.",
    )
    parser.add_argument("--crawler-python-path")
    parser.add_argument("--crawler-timeout-seconds", type=float)
    parser.add_argument("--no-resume-existing", action="store_true")
    args = parser.parse_args()

    result = run_targeted_backfill(
        db_path=args.db_path,
        output_dir=args.output_dir,
        fixture_ids=args.fixture_id or None,
        local_date=args.local_date,
        available_at=args.available_at,
        local_utc_offset_hours=args.local_utc_offset_hours,
        skill_scripts_dir=args.skill_scripts_dir,
        recent_match_limit=args.recent_match_limit,
        recent_club_match_limit=args.recent_club_match_limit,
        max_players_per_team=args.max_players_per_team,
        page_timeout_ms=args.page_timeout_ms,
        bookmaker_id=args.bookmaker_id,
        game_link_limit=args.game_link_limit,
        source_mode=args.source_mode,
        crawler_python_path=args.crawler_python_path,
        crawler_timeout_seconds=args.crawler_timeout_seconds,
        resume_existing=not args.no_resume_existing,
    )
    _emit_json(result)


if __name__ == "__main__":
    main()
