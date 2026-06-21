from __future__ import annotations

import argparse
import html
import importlib.util
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from app.config import Settings, load_settings

from .provider_contracts import BaseProvider, ProviderResult
from .world_cup_2026_bootstrap import DEFAULT_OUTPUT_DIR, read_csv, read_json, write_json
from .world_cup_2026_recent_results import DEFAULT_SKILL_SCRIPTS_DIR

DEFAULT_BOOKMAKER_ID = 549
DEFAULT_GAME_LINK_LIMIT = 200
CRAWLER_ODDS_SOURCE = "soccerway_odds_api"
API_ODDS_SOURCE = "the_odds_api"
ODDS_MARKET_TYPES = ("h2h", "spreads", "totals")
ODDS_SNAPSHOT_REQUIRED_FIELDS = (
    "snapshot_id",
    "fixture_id",
    "match_id",
    "market_type",
    "captured_at",
    "source",
    "source_event_id",
    "source_game_url",
    "source_page_url",
    "bookmaker_id",
    "bookmaker_name",
    "status",
    "available_at",
)
SOCCERWAY_WORLD_CUP_URL = "https://us.soccerway.com/world/world-championship/"
SOCCERWAY_ODDS_API_URL = "https://global.ds.lsapp.eu/odds/pq_graphql"


class TheOddsApiProvider(BaseProvider):
    name = "the_odds_api"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_recent_results(self, team_id: str) -> ProviderResult:
        return ProviderResult.unsupported(provider=self.name, capability="recent_results")

    def get_player_form(self, player_id: str) -> ProviderResult:
        return ProviderResult.unsupported(provider=self.name, capability="player_form")

    def list_odds_events(self) -> ProviderResult:
        try:
            events, quota = _fetch_the_odds_api_events(settings=self.settings)
        except RuntimeError as exc:
            message = str(exc)
            status = "missing" if "missing_key" in message else "failed"
            return ProviderResult(
                status=status,
                diagnostics={
                    "provider": self.name,
                    "capability": "odds",
                    "reason": message,
                },
            )
        return ProviderResult(
            status="ok" if events else "missing",
            data=[item for item in events if isinstance(item, dict)],
            diagnostics={
                "provider": self.name,
                "capability": "odds",
                "quota": quota,
            },
        )

    def get_odds(self, match_id: str) -> ProviderResult:
        result = self.list_odds_events()
        if result.status != "ok":
            return ProviderResult(
                status=result.status,
                diagnostics={**result.diagnostics, "entity_id": match_id},
            )
        event = next(
            (
                item
                for item in result.data
                if isinstance(item, dict) and str(item.get("id") or "") == match_id
            ),
            None,
        )
        if event is None:
            return ProviderResult.missing(
                provider=self.name,
                capability="odds",
                reason="event_not_found",
            )
        return ProviderResult(
            status="ok",
            data=[event],
            diagnostics={
                "provider": self.name,
                "capability": "odds",
                "entity_id": match_id,
                "quota": result.diagnostics.get("quota", {}),
            },
        )


SOCCERWAY_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "bosnia and herzegovina": ("Bosnia & Herzegovina",),
    "cabo verde": ("Cape Verde",),
    "congo dr": ("DR Congo", "D.R. Congo"),
    "cote d ivoire": ("Ivory Coast",),
    "czechia": ("Czech Republic",),
    "ir iran": ("Iran",),
    "korea republic": ("South Korea",),
    "turkiye": ("Turkey",),
}


def build_soccerway_candidate(
    *,
    game_url: str,
    page_url: str,
    event_id: str,
    match_payload: dict[str, Any],
) -> dict[str, Any] | None:
    home_team = html.unescape(str(match_payload.get("home_team") or "").strip())
    away_team = html.unescape(str(match_payload.get("away_team") or "").strip())
    match_date = _display_date_to_iso(str(match_payload.get("date_display") or ""))
    if not home_team or not away_team or not match_date:
        return None
    return {
        "game_url": game_url.rstrip("/") + "/",
        "page_url": page_url,
        "event_id": event_id,
        "home_team": home_team,
        "away_team": away_team,
        "match_date": match_date,
        "home_key": _normalize_name(home_team),
        "away_key": _normalize_name(away_team),
    }


def match_fixture_to_candidate(
    fixture_row: dict[str, str],
    *,
    team_aliases_by_id: dict[str, set[str]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    fixture_date = _match_time_to_date(str(fixture_row.get("match_time") or ""))
    home_aliases = team_aliases_by_id.get(str(fixture_row.get("home_team_id") or ""), set())
    away_aliases = team_aliases_by_id.get(str(fixture_row.get("away_team_id") or ""), set())
    if not fixture_date or not home_aliases or not away_aliases:
        return None

    matched = [
        candidate
        for candidate in candidates
        if candidate.get("match_date") == fixture_date
        and candidate.get("home_key") in home_aliases
        and candidate.get("away_key") in away_aliases
    ]
    if not matched:
        return None
    matched.sort(key=lambda item: (item["page_url"], item["event_id"]))
    return matched[0]


def normalize_odds_payload_to_rows(
    *,
    fixture_id: str,
    odds_record: dict[str, Any],
    captured_at: str,
) -> list[dict[str, Any]]:
    bookmaker = odds_record.get("bookmaker") if isinstance(odds_record.get("bookmaker"), dict) else {}
    common = {
        "fixture_id": fixture_id,
        "match_id": fixture_id,
        "captured_at": captured_at,
        "available_at": captured_at,
        "source": str(odds_record.get("source") or CRAWLER_ODDS_SOURCE),
        "source_event_id": str(odds_record.get("event_id") or ""),
        "source_game_url": str(odds_record.get("game_url") or ""),
        "source_page_url": str(odds_record.get("page_url") or ""),
        "bookmaker_id": int(bookmaker.get("id") or DEFAULT_BOOKMAKER_ID),
        "bookmaker_name": str(bookmaker.get("name") or ""),
        "bookmakers_count": 1,
    }

    rows = []
    h2h_row = _normalize_h2h_market(common=common, fixture_id=fixture_id, odds_record=odds_record)
    if h2h_row is not None:
        rows.append(h2h_row)
    spreads_row = _normalize_spreads_market(common=common, fixture_id=fixture_id, odds_record=odds_record)
    if spreads_row is not None:
        rows.append(spreads_row)
    totals_row = _normalize_totals_market(common=common, fixture_id=fixture_id, odds_record=odds_record)
    if totals_row is not None:
        rows.append(totals_row)
    return rows


async def collect_world_cup_odds(
    *,
    output_dir: Path,
    accessed_at: str,
    bookmaker_id: int,
    game_link_limit: int,
    skill_scripts_dir: Path,
    only_fixture_ids: set[str] | None,
    preserve_existing: bool,
    source_mode: str = "crawler",
) -> dict[str, Any]:
    if source_mode not in {"api", "crawler"}:
        raise ValueError(f"unsupported_odds_source_mode:{source_mode}")
    team_rows = read_csv(output_dir / "world_cup_2026_teams.csv")
    fixture_rows = read_csv(output_dir / "world_cup_2026_fixtures.csv")
    target_fixture_rows = fixture_rows[:]
    if only_fixture_ids:
        target_fixture_rows = [row for row in target_fixture_rows if row["fixture_id"] in only_fixture_ids]

    skill_module = None
    game_urls: list[str] = []
    quota: dict[str, Any] = {}
    preferred_bookmaker_keys: tuple[str, ...] = ()
    if source_mode == "api":
        settings = load_settings()
        preferred_bookmaker_keys = _split_csv(settings.the_odds_api_bookmakers)
        provider_result = TheOddsApiProvider(settings).list_odds_events()
        if provider_result.status != "ok":
            raise RuntimeError(
                str(provider_result.diagnostics.get("reason") or "the_odds_api_events_unavailable")
            )
        quota = dict(provider_result.diagnostics.get("quota") or {})
        candidate_index = build_the_odds_api_candidate_index(provider_result.data)
    else:
        skill_module = _load_soccerway_module(skill_scripts_dir)
        game_urls = skill_module.extract_world_cup_game_links(limit=max(game_link_limit, len(target_fixture_rows)))
        candidate_index = build_soccerway_candidate_index(game_urls, skill_module=skill_module)
    team_aliases_by_id = _team_aliases_by_id(team_rows)

    existing_diagnostics_payload = _existing_diagnostics_payload(output_dir, preserve_existing=preserve_existing)
    existing_rows_by_fixture = _existing_rows_by_fixture(output_dir, preserve_existing=preserve_existing)
    existing_diagnostics_by_fixture = _existing_diagnostics_by_fixture(output_dir, preserve_existing=preserve_existing)
    remaining_fixture_ids = {row["fixture_id"] for row in target_fixture_rows}
    final_rows = [
        row
        for fixture_id, rows in existing_rows_by_fixture.items()
        if fixture_id not in remaining_fixture_ids
        for row in rows
    ]
    diagnostics = [
        item
        for fixture_id, item in existing_diagnostics_by_fixture.items()
        if fixture_id not in remaining_fixture_ids
    ]

    for fixture_row in target_fixture_rows:
        fixture_id = fixture_row["fixture_id"]
        existing_rows = existing_rows_by_fixture.get(fixture_id, [])
        context = _fixture_context(fixture_row, team_rows)
        candidate = match_fixture_to_candidate(
            fixture_row,
            team_aliases_by_id=team_aliases_by_id,
            candidates=candidate_index["candidates"],
        )
        if candidate is None:
            diagnostic = {
                **context,
                "status": "no_matching_odds_event_available",
                "source_mode": source_mode,
                "preserved_existing_rows": len(existing_rows),
            }
            if existing_rows:
                final_rows.extend(existing_rows)
            diagnostics.append(diagnostic)
            continue

        try:
            if source_mode == "api":
                odds_record = _build_the_odds_api_record(
                    candidate["event"],
                    bookmaker_id=bookmaker_id,
                    preferred_bookmaker_keys=preferred_bookmaker_keys,
                )
            else:
                odds_record = skill_module.fetch_world_cup_match_odds(
                    candidate["game_url"],
                    bookmaker_id=bookmaker_id,
                )
        except Exception as exc:  # noqa: BLE001
            diagnostic = {
                **context,
                "status": "fetch_failed",
                "source_mode": source_mode,
                "source_game_url": candidate["game_url"],
                "source_page_url": candidate["page_url"],
                "source_event_id": candidate["event_id"],
                "error": str(exc),
                "preserved_existing_rows": len(existing_rows),
            }
            if existing_rows:
                final_rows.extend(existing_rows)
            diagnostics.append(diagnostic)
            continue

        rows = normalize_odds_payload_to_rows(
            fixture_id=fixture_id,
            odds_record=odds_record,
            captured_at=accessed_at,
        )
        if rows:
            final_rows.extend(rows)
            diagnostics.append({
                **context,
                "status": "saved",
                "source_mode": source_mode,
                "source_game_url": str(odds_record.get("game_url") or candidate["game_url"]),
                "source_page_url": str(odds_record.get("page_url") or candidate["page_url"]),
                "source_event_id": str(odds_record.get("event_id") or candidate["event_id"]),
                "snapshot_rows": len(rows),
                "market_types_saved": sorted(row["market_type"] for row in rows),
                "missing_fields": sorted(set(odds_record.get("missing_fields") or [])),
                "preserved_existing_rows": 0,
            })
            continue

        if existing_rows:
            final_rows.extend(existing_rows)
        diagnostics.append({
            **context,
            "status": "no_current_odds_available",
            "source_mode": source_mode,
            "source_game_url": str(odds_record.get("game_url") or candidate["game_url"]),
            "source_page_url": str(odds_record.get("page_url") or candidate["page_url"]),
            "source_event_id": str(odds_record.get("event_id") or candidate["event_id"]),
            "snapshot_rows": 0,
            "market_types_saved": [],
            "missing_fields": sorted(set(odds_record.get("missing_fields") or [])),
            "preserved_existing_rows": len(existing_rows),
        })

    final_rows.sort(key=lambda item: (item["fixture_id"], item["market_type"]))
    diagnostics.sort(key=lambda item: item["fixture_id"])
    write_json(output_dir / "odds_snapshots.json", final_rows)
    coverage_accessed_at = accessed_at
    if only_fixture_ids and isinstance(existing_diagnostics_payload, dict):
        coverage_accessed_at = str(
            existing_diagnostics_payload.get("coverage_accessed_at")
            or existing_diagnostics_payload.get("accessed_at")
            or accessed_at
        )
    diagnostics_payload = {
        "accessed_at": coverage_accessed_at,
        "coverage_accessed_at": coverage_accessed_at,
        "latest_run_accessed_at": accessed_at,
        "latest_run_scope": "full" if len(target_fixture_rows) == len(diagnostics) else "subset",
        "bookmaker_id": bookmaker_id,
        "bookmaker_name": getattr(skill_module, "BOOKMAKER_NAMES", {}).get(bookmaker_id) if skill_module else "",
        "source_mode": source_mode,
        "target_fixtures": len(diagnostics),
        "run_target_fixtures": len(target_fixture_rows),
        "rows_written": len(final_rows),
        "preserve_existing": preserve_existing,
        "selected_fixture_filter": sorted(only_fixture_ids) if only_fixture_ids else [],
        "discovered_game_links": len(game_urls),
        "indexed_candidates": len(candidate_index["candidates"]),
        "candidate_fetch_errors": candidate_index["errors"],
        "quota": quota,
        "diagnostics": diagnostics,
    }
    write_json(output_dir / "odds_diagnostics.json", diagnostics_payload)
    _update_manifest(output_dir / "source_manifest.json")
    return diagnostics_payload


def build_soccerway_candidate_index(
    game_urls: list[str],
    *,
    skill_module: Any,
) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update(getattr(skill_module, "DEFAULT_HEADERS", {}))
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for game_url in game_urls:
        page_url = skill_module.odds_page_url(game_url)
        try:
            html = skill_module.fetch_text(page_url, session=session)
            title_match = re.search(r"<title>(.*?)</title>", html, re.S)
            title = title_match.group(1).strip() if title_match else page_url
            event_id = skill_module.extract_event_id(html)
            candidate = build_soccerway_candidate(
                game_url=game_url,
                page_url=page_url,
                event_id=event_id,
                match_payload=skill_module.parse_match_title(title),
            )
            if candidate is not None:
                candidates.append(candidate)
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "game_url": game_url,
                "page_url": page_url,
                "error": str(exc),
            })
    return {
        "candidates": candidates,
        "errors": errors,
    }


def build_the_odds_api_candidate_index(events: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for event in events:
        candidate = build_the_odds_api_candidate(event)
        if candidate is not None:
            candidates.append(candidate)
    return {
        "candidates": candidates,
        "errors": [],
    }


def build_the_odds_api_candidate(event: dict[str, Any]) -> dict[str, Any] | None:
    event_id = str(event.get("id") or "").strip()
    home_team = html.unescape(str(event.get("home_team") or "").strip())
    away_team = html.unescape(str(event.get("away_team") or "").strip())
    match_date = _match_time_to_date(str(event.get("commence_time") or ""))
    if not event_id or not home_team or not away_team or not match_date:
        return None
    return {
        "event": event,
        "game_url": "",
        "page_url": "",
        "event_id": event_id,
        "home_team": home_team,
        "away_team": away_team,
        "match_date": match_date,
        "home_key": _normalize_name(home_team),
        "away_key": _normalize_name(away_team),
    }


def _fetch_the_odds_api_events(*, settings: Any | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = settings or load_settings()
    if not settings.the_odds_api_key:
        raise RuntimeError("the_odds_api_missing_key:THE_ODDS_API_KEY")
    url = f"{settings.the_odds_api_base_url.rstrip('/')}/sports/{settings.the_odds_api_sport_key}/odds"
    params = {
        "apiKey": settings.the_odds_api_key,
        "markets": settings.the_odds_api_markets,
        "oddsFormat": settings.the_odds_api_odds_format,
    }
    if settings.the_odds_api_bookmakers:
        params["bookmakers"] = settings.the_odds_api_bookmakers
    else:
        params["regions"] = settings.the_odds_api_regions
    response = requests.get(url, params=params, timeout=10)
    if response.status_code >= 400:
        raise RuntimeError(f"the_odds_api_http_{response.status_code}:{response.text[:200]}")
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("the_odds_api_invalid_payload:list_required")
    return payload, {
        "requests_remaining": str(response.headers.get("x-requests-remaining", "")),
        "requests_used": str(response.headers.get("x-requests-used", "")),
        "requests_last": str(response.headers.get("x-requests-last", "")),
    }


def _build_the_odds_api_record(
    event: dict[str, Any],
    *,
    bookmaker_id: int,
    preferred_bookmaker_keys: tuple[str, ...],
) -> dict[str, Any]:
    bookmaker = _select_bookmaker(event, preferred_bookmaker_keys=preferred_bookmaker_keys)
    return {
        "source": API_ODDS_SOURCE,
        "event_id": str(event.get("id") or ""),
        "game_url": "",
        "page_url": "",
        "bookmaker": {
            "id": bookmaker_id,
            "name": str(bookmaker.get("title") or bookmaker.get("key") or "the_odds_api"),
        },
        "european_odds": _extract_h2h_market(event, bookmaker),
        "asian_handicap": _extract_spreads_market(event, bookmaker),
        "totals": _extract_totals_market(bookmaker),
        "missing_fields": [],
    }


def _select_bookmaker(event: dict[str, Any], *, preferred_bookmaker_keys: tuple[str, ...]) -> dict[str, Any]:
    bookmakers = event.get("bookmakers")
    if not isinstance(bookmakers, list) or not bookmakers:
        raise RuntimeError("the_odds_api_missing_bookmakers")
    candidates = [bookmaker for bookmaker in bookmakers if isinstance(bookmaker, dict)]
    if not candidates:
        raise RuntimeError("the_odds_api_invalid_bookmakers")
    if preferred_bookmaker_keys:
        preferred = {_normalize_name(key) for key in preferred_bookmaker_keys}
        for bookmaker in candidates:
            bookmaker_keys = {
                _normalize_name(bookmaker.get("key")),
                _normalize_name(bookmaker.get("title")),
            }
            if bookmaker_keys & preferred:
                return bookmaker
    candidates.sort(
        key=lambda item: (
            -_supported_market_count(item),
            str(item.get("title") or item.get("key") or ""),
        )
    )
    return candidates[0]


def _supported_market_count(bookmaker: dict[str, Any]) -> int:
    markets = bookmaker.get("markets")
    if not isinstance(markets, list):
        return 0
    return len(
        {
            str(market.get("key") or "")
            for market in markets
            if isinstance(market, dict) and str(market.get("key") or "") in ODDS_MARKET_TYPES
        }
    )


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def _extract_market(bookmaker: dict[str, Any], market_key: str) -> dict[str, Any]:
    markets = bookmaker.get("markets")
    if not isinstance(markets, list):
        return {}
    for market in markets:
        if isinstance(market, dict) and str(market.get("key") or "") == market_key:
            return market
    return {}


def _extract_h2h_market(event: dict[str, Any], bookmaker: dict[str, Any]) -> dict[str, Any]:
    market = _extract_market(bookmaker, "h2h")
    outcomes = market.get("outcomes")
    if not isinstance(outcomes, list):
        return {}
    prices = {
        _normalize_name(item.get("name")): _coerce_float(item.get("price"))
        for item in outcomes
        if isinstance(item, dict)
    }
    home_key = _normalize_name(event.get("home_team"))
    away_key = _normalize_name(event.get("away_team"))
    return {
        "home": {"value": prices[home_key]} if prices.get(home_key) is not None else None,
        "draw": {"value": prices["draw"]} if prices.get("draw") is not None else None,
        "away": {"value": prices[away_key]} if prices.get(away_key) is not None else None,
    }


def _extract_spreads_market(event: dict[str, Any], bookmaker: dict[str, Any]) -> dict[str, Any]:
    return _extract_two_way_line_market(
        _extract_market(bookmaker, "spreads"),
        left_name=str(event.get("home_team") or ""),
        right_name=str(event.get("away_team") or ""),
        left_key="home",
        right_key="away",
    )


def _extract_totals_market(bookmaker: dict[str, Any]) -> dict[str, Any]:
    return _extract_two_way_line_market(
        _extract_market(bookmaker, "totals"),
        left_name="Over",
        right_name="Under",
        left_key="over",
        right_key="under",
    )


def _extract_two_way_line_market(
    market: dict[str, Any],
    *,
    left_name: str,
    right_name: str,
    left_key: str,
    right_key: str,
) -> dict[str, Any]:
    outcomes = market.get("outcomes")
    if not isinstance(outcomes, list):
        return {}
    pairs: dict[float, dict[str, float | None]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        point = _coerce_float(outcome.get("point"))
        price = _coerce_float(outcome.get("price"))
        if point is None or price is None:
            continue
        pair = pairs.setdefault(point, {left_key: None, right_key: None})
        name = _normalize_name(outcome.get("name"))
        if name == _normalize_name(left_name):
            pair[left_key] = price
        elif name == _normalize_name(right_name):
            pair[right_key] = price
    for point, pair in sorted(pairs.items()):
        if pair[left_key] is None or pair[right_key] is None:
            continue
        return {
            "selected_line": {
                "handicap": point,
                left_key: {"value": pair[left_key]} if pair[left_key] is not None else None,
                right_key: {"value": pair[right_key]} if pair[right_key] is not None else None,
            }
        }
    for point, pair in sorted(pairs.items()):
        if pair[left_key] is None and pair[right_key] is None:
            continue
        return {
            "selected_line": {
                "handicap": point,
                left_key: {"value": pair[left_key]} if pair[left_key] is not None else None,
                right_key: {"value": pair[right_key]} if pair[right_key] is not None else None,
            }
        }
    return {}


def _normalize_h2h_market(
    *,
    common: dict[str, Any],
    fixture_id: str,
    odds_record: dict[str, Any],
) -> dict[str, Any] | None:
    market = odds_record.get("european_odds") if isinstance(odds_record.get("european_odds"), dict) else {}
    home_odds = _coerce_float(market.get("home"), key="value")
    draw_odds = _coerce_float(market.get("draw"), key="value")
    away_odds = _coerce_float(market.get("away"), key="value")
    if home_odds is None and draw_odds is None and away_odds is None:
        return None
    missing_fields = []
    if home_odds is None:
        missing_fields.append("home_odds_missing")
    if draw_odds is None:
        missing_fields.append("draw_odds_missing")
    if away_odds is None:
        missing_fields.append("away_odds_missing")
    return {
        **common,
        "snapshot_id": f"odds_{fixture_id}_h2h_{common['bookmaker_id']}",
        "market_type": "h2h",
        "home_odds": home_odds,
        "draw_odds": draw_odds,
        "away_odds": away_odds,
        "spread_line": None,
        "home_water": None,
        "away_water": None,
        "total_goals_line": None,
        "over_water": None,
        "under_water": None,
        "status": "ok" if not missing_fields else "partial",
        "missing_fields": missing_fields,
    }


def _normalize_spreads_market(
    *,
    common: dict[str, Any],
    fixture_id: str,
    odds_record: dict[str, Any],
) -> dict[str, Any] | None:
    market = odds_record.get("asian_handicap") if isinstance(odds_record.get("asian_handicap"), dict) else {}
    selected_line = market.get("selected_line") if isinstance(market.get("selected_line"), dict) else {}
    spread_line = _coerce_float(selected_line.get("handicap"))
    home_water = _coerce_float(selected_line.get("home"), key="value")
    away_water = _coerce_float(selected_line.get("away"), key="value")
    if spread_line is None and home_water is None and away_water is None:
        return None
    missing_fields = []
    if spread_line is None:
        missing_fields.append("spread_line_missing")
    if home_water is None:
        missing_fields.append("home_water_missing")
    if away_water is None:
        missing_fields.append("away_water_missing")
    return {
        **common,
        "snapshot_id": f"odds_{fixture_id}_spreads_{common['bookmaker_id']}",
        "market_type": "spreads",
        "home_odds": None,
        "draw_odds": None,
        "away_odds": None,
        "spread_line": spread_line,
        "home_water": home_water,
        "away_water": away_water,
        "total_goals_line": None,
        "over_water": None,
        "under_water": None,
        "status": "ok" if not missing_fields else "partial",
        "missing_fields": missing_fields,
    }


def _normalize_totals_market(
    *,
    common: dict[str, Any],
    fixture_id: str,
    odds_record: dict[str, Any],
) -> dict[str, Any] | None:
    market = odds_record.get("totals") if isinstance(odds_record.get("totals"), dict) else {}
    selected_line = market.get("selected_line") if isinstance(market.get("selected_line"), dict) else {}
    total_goals_line = _coerce_float(selected_line.get("handicap"))
    over_water = _coerce_float(selected_line.get("over"), key="value")
    under_water = _coerce_float(selected_line.get("under"), key="value")
    if total_goals_line is None and over_water is None and under_water is None:
        return None
    missing_fields = []
    if total_goals_line is None:
        missing_fields.append("total_goals_line_missing")
    if over_water is None:
        missing_fields.append("over_water_missing")
    if under_water is None:
        missing_fields.append("under_water_missing")
    return {
        **common,
        "snapshot_id": f"odds_{fixture_id}_totals_{common['bookmaker_id']}",
        "market_type": "totals",
        "home_odds": None,
        "draw_odds": None,
        "away_odds": None,
        "spread_line": None,
        "home_water": None,
        "away_water": None,
        "total_goals_line": total_goals_line,
        "over_water": over_water,
        "under_water": under_water,
        "status": "ok" if not missing_fields else "partial",
        "missing_fields": missing_fields,
    }


def _existing_rows_by_fixture(output_dir: Path, *, preserve_existing: bool) -> dict[str, list[dict[str, Any]]]:
    path = output_dir / "odds_snapshots.json"
    if not preserve_existing or not path.exists():
        return {}
    payload = read_json(path)
    rows_by_fixture: dict[str, list[dict[str, Any]]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        fixture_id = str(item.get("fixture_id") or "")
        if fixture_id:
            rows_by_fixture.setdefault(fixture_id, []).append(item)
    return rows_by_fixture


def _existing_diagnostics_payload(output_dir: Path, *, preserve_existing: bool) -> dict[str, Any]:
    path = output_dir / "odds_diagnostics.json"
    if not preserve_existing or not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _existing_diagnostics_by_fixture(output_dir: Path, *, preserve_existing: bool) -> dict[str, dict[str, Any]]:
    payload = _existing_diagnostics_payload(output_dir, preserve_existing=preserve_existing)
    diagnostics = payload.get("diagnostics", []) if isinstance(payload, dict) else []
    by_fixture: dict[str, dict[str, Any]] = {}
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        fixture_id = str(item.get("fixture_id") or "")
        if fixture_id:
            by_fixture[fixture_id] = item
    return by_fixture


def _fixture_context(fixture_row: dict[str, str], team_rows: list[dict[str, str]]) -> dict[str, Any]:
    by_team_id = {row["team_id"]: row for row in team_rows}
    home_team = by_team_id.get(fixture_row["home_team_id"], {})
    away_team = by_team_id.get(fixture_row["away_team_id"], {})
    return {
        "fixture_id": fixture_row["fixture_id"],
        "match_date": _match_time_to_date(str(fixture_row.get("match_time") or "")),
        "home_team": str(home_team.get("canonical_name") or fixture_row.get("home_team_id") or ""),
        "away_team": str(away_team.get("canonical_name") or fixture_row.get("away_team_id") or ""),
    }


def _team_aliases_by_id(team_rows: list[dict[str, str]]) -> dict[str, set[str]]:
    aliases_by_id: dict[str, set[str]] = {}
    for row in team_rows:
        team_id = row["team_id"]
        aliases = {
            _normalize_name(str(row.get("canonical_name") or "")),
        }
        for alias in str(row.get("aliases") or "").split("|"):
            normalized = _normalize_name(alias)
            if normalized:
                aliases.add(normalized)
        normalized_name = _normalize_name(str(row.get("canonical_name") or ""))
        for alias in SOCCERWAY_TEAM_ALIASES.get(normalized_name, ()):
            normalized = _normalize_name(alias)
            if normalized:
                aliases.add(normalized)
        aliases_by_id[team_id] = {alias for alias in aliases if alias}
    return aliases_by_id


def _load_soccerway_module(skill_scripts_dir: Path):
    module_path = skill_scripts_dir / "soccerway_odds.py"
    module_name = "wc2026_soccerway_odds_skill"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _update_manifest(path: Path) -> None:
    manifest = read_json(path)
    odds_rows = read_json(path.parent / "odds_snapshots.json")
    diagnostics_payload = read_json(path.parent / "odds_diagnostics.json")
    manifest["odds_file"] = "odds_snapshots.json"
    manifest["odds_diagnostics_file"] = "odds_diagnostics.json"
    coverage = manifest.get("coverage_summary") if isinstance(manifest.get("coverage_summary"), dict) else {}
    coverage["odds_snapshots"] = len([item for item in odds_rows if isinstance(item, dict)])
    coverage["odds_fixtures_mapped"] = len({
        item.get("fixture_id")
        for item in odds_rows
        if isinstance(item, dict) and item.get("fixture_id")
    })
    coverage["odds_fixtures_audited"] = len([
        item
        for item in diagnostics_payload.get("diagnostics", [])
        if isinstance(item, dict)
    ])
    manifest["coverage_summary"] = coverage
    schema_gaps = manifest.get("schema_gaps") if isinstance(manifest.get("schema_gaps"), dict) else {}
    schema_gaps["odds_snapshots_pending"] = False
    if path.parent.joinpath("national_recent_results.csv").exists():
        schema_gaps["national_recent_results_pending"] = len(read_csv(path.parent / "national_recent_results.csv")) == 0
    manifest["schema_gaps"] = schema_gaps
    source_urls = manifest.get("source_urls") if isinstance(manifest.get("source_urls"), list) else []
    for url in (SOCCERWAY_WORLD_CUP_URL, SOCCERWAY_ODDS_API_URL):
        if url not in source_urls:
            source_urls.append(url)
    manifest["source_urls"] = source_urls
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _display_date_to_iso(value: str) -> str:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


def _match_time_to_date(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _coerce_float(value: Any, *, key: str | None = None) -> float | None:
    if isinstance(value, dict):
        value = value.get(key) if key else None
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ascii_fold(value: Any) -> str:
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")


def _normalize_name(value: Any) -> str:
    ascii_value = _ascii_fold(value).casefold()
    cleaned = re.sub(r"[^a-z0-9]+", " ", ascii_value)
    return " ".join(cleaned.split())


def _emit_json(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        return
    print(text, end="")


async def _async_main(args: argparse.Namespace) -> None:
    result = await collect_world_cup_odds(
        output_dir=args.output_dir,
        accessed_at=args.accessed_at,
        bookmaker_id=args.bookmaker_id,
        game_link_limit=args.game_link_limit,
        skill_scripts_dir=args.skill_scripts_dir,
        only_fixture_ids=set(args.fixture_id or []) or None,
        preserve_existing=not args.replace_existing,
        source_mode=args.source_mode,
    )
    _emit_json(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--accessed-at", required=True)
    parser.add_argument("--bookmaker-id", type=int, default=DEFAULT_BOOKMAKER_ID)
    parser.add_argument("--game-link-limit", type=int, default=DEFAULT_GAME_LINK_LIMIT)
    parser.add_argument("--source-mode", choices=("api", "crawler"), default="crawler")
    parser.add_argument("--skill-scripts-dir", type=Path, default=DEFAULT_SKILL_SCRIPTS_DIR)
    parser.add_argument("--fixture-id", action="append", default=[])
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()

    import asyncio

    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
