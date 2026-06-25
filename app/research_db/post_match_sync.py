from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Protocol

from app.storage.repository import utc_now

from .repository import ResearchDatabaseRepository


CLOSED_EVENT_STATUSES = {
    "closed",
    "ended",
    "finished",
    "complete",
    "completed",
    "after_extra_time",
    "after_penalties",
}


class PostMatchProvider(Protocol):
    source: str

    def fetch_sport_event_summary(self, sport_event_id: str) -> Any:
        ...

    def fetch_sport_event_lineups(self, sport_event_id: str) -> Any:
        ...

    def fetch_extended_sport_event_summary(self, sport_event_id: str) -> Any:
        ...


PostMatchReviewCallback = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any] | None],
    dict[str, Any],
]


@dataclass(frozen=True)
class AppearanceNormalization:
    rows: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    lineup_status: str
    appearance_status: str


class PostMatchSyncService:
    def __init__(
        self,
        repository: ResearchDatabaseRepository,
        provider: PostMatchProvider,
        *,
        review_callback: PostMatchReviewCallback | None = None,
        now_fn: Callable[[], str] = utc_now,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.review_callback = review_callback
        self.now_fn = now_fn

    def sync_recent_completed_matches(
        self,
        *,
        lookback_hours: int = 48,
        delay_minutes: int = 30,
        max_fixtures: int = 20,
        fixture_ids: list[str] | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        now = _parse_datetime(self.now_fn())
        candidates = self._candidate_fixtures(
            now=now,
            lookback_hours=lookback_hours,
            delay_minutes=delay_minutes,
            max_fixtures=max_fixtures,
            fixture_ids=fixture_ids,
        )
        fixture_reports: list[dict[str, Any]] = []
        for fixture in candidates:
            try:
                fixture_reports.append(
                    self._sync_fixture(
                        fixture,
                        dry_run=dry_run,
                        force=force,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                fixture_reports.append(
                    {
                        "fixture_id": str(fixture.get("fixture_id") or ""),
                        "status": "failed",
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                        "data_quality": {
                            "result": "missing",
                            "lineup": "missing",
                            "player_appearances": "missing",
                            "prediction_snapshot": "missing",
                            "post_match_review": "failed",
                        },
                    }
                )

        status_counts = {
            status: sum(1 for item in fixture_reports if item.get("status") == status)
            for status in ("ok", "partial", "failed", "skipped")
        }
        if status_counts["failed"] and status_counts["failed"] == len(fixture_reports):
            overall_status = "failed"
        elif status_counts["failed"] or status_counts["partial"]:
            overall_status = "partial"
        else:
            overall_status = "ok"
        return {
            "status": overall_status,
            "dry_run": dry_run,
            "provider": str(getattr(self.provider, "source", "unknown")),
            "requested_fixture_ids": list(fixture_ids or []),
            "candidate_count": len(candidates),
            "fixture_count": len(fixture_reports),
            "status_counts": status_counts,
            "fixtures": fixture_reports,
        }

    def _candidate_fixtures(
        self,
        *,
        now: datetime,
        lookback_hours: int,
        delay_minutes: int,
        max_fixtures: int,
        fixture_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        if fixture_ids:
            fixtures = [self.repository.get_fixture(fixture_id) for fixture_id in fixture_ids]
            return [fixture for fixture in fixtures if fixture is not None][:max_fixtures]

        earliest = now - timedelta(hours=max(lookback_hours, 0))
        latest = now - timedelta(minutes=max(delay_minutes, 0))
        candidates: list[dict[str, Any]] = []
        for fixture in self.repository.list_fixtures():
            match_time = _parse_datetime_or_none(fixture.get("match_time"))
            if match_time is None:
                continue
            if earliest <= match_time <= latest:
                candidates.append(fixture)
        candidates.sort(key=lambda item: str(item.get("match_time") or ""))
        return candidates[:max_fixtures]

    def _sync_fixture(
        self,
        fixture: dict[str, Any],
        *,
        dry_run: bool,
        force: bool,
    ) -> dict[str, Any]:
        fixture_id = str(fixture["fixture_id"])
        source_fixture_id = str(fixture.get("source_fixture_id") or fixture_id)
        existing_result = self.repository.get_match_result(fixture_id)
        existing_appearances = self.repository.list_player_match_appearances(fixture_id=fixture_id)
        coverage_audit = self.repository.latest_audit_record(
            "post_match_sync_coverage",
            fixture_id,
        )
        if (
            existing_result
            and _appearance_rows_complete(existing_appearances)
            and _coverage_audit_complete(coverage_audit)
            and not force
        ):
            prediction = self.repository.latest_pre_match_prediction(
                fixture_id,
                before_generated_at=str(fixture.get("match_time") or ""),
            )
            return {
                "fixture_id": fixture_id,
                "status": "skipped",
                "reason": "completed_result_and_appearances_already_present",
                "data_quality": {
                    "result": "ok",
                    "lineup": "ok",
                    "player_appearances": "ok",
                    "prediction_snapshot": "ok" if prediction else "missing",
                    "post_match_review": "skipped",
                },
                "writes": {
                    "match_results": 0,
                    "player_match_appearances": 0,
                },
            }

        summary_result = self.provider.fetch_sport_event_summary(source_fixture_id)
        summary_payload, summary_error = _adapter_payload(summary_result)
        if summary_error:
            return _provider_failure_report(fixture_id, "summary", summary_error)
        result_row = normalize_completed_match_result(
            summary_payload,
            fixture=fixture,
            source=str(getattr(self.provider, "source", "post_match_provider")),
            source_fixture_id=source_fixture_id,
            available_at=self.now_fn(),
        )
        if result_row is None:
            return {
                "fixture_id": fixture_id,
                "status": "skipped",
                "reason": "event_not_closed",
                "data_quality": {
                    "result": "missing",
                    "lineup": "missing",
                    "player_appearances": "missing",
                    "prediction_snapshot": "missing",
                    "post_match_review": "skipped",
                },
                "writes": {
                    "match_results": 0,
                    "player_match_appearances": 0,
                },
            }

        lineup_payload: Any = {}
        lineup_error: str | None = None
        try:
            lineup_result = self.provider.fetch_sport_event_lineups(source_fixture_id)
            lineup_payload, lineup_error = _adapter_payload(lineup_result)
        except AttributeError:
            lineup_error = "provider_lineup_capability_missing"

        extended_payload: Any = {}
        extended_error: str | None = None
        try:
            extended_result = self.provider.fetch_extended_sport_event_summary(source_fixture_id)
            extended_payload, extended_error = _adapter_payload(extended_result)
        except AttributeError:
            extended_error = "provider_extended_summary_capability_missing"

        appearances = normalize_player_match_appearances(
            repository=self.repository,
            fixture=fixture,
            summary_payload=summary_payload,
            lineup_payload=lineup_payload,
            extended_payload=extended_payload,
            source=str(getattr(self.provider, "source", "post_match_provider")),
            source_fixture_id=source_fixture_id,
            available_at=result_row["available_at"],
        )
        diagnostics = list(appearances.diagnostics)
        if lineup_error:
            diagnostics.append({"status": "missing", "scope": "lineup", "reason": lineup_error})
        if extended_error:
            diagnostics.append({"status": "partial", "scope": "player_statistics", "reason": extended_error})

        prediction = self.repository.latest_pre_match_prediction(
            fixture_id,
            before_generated_at=str(fixture.get("match_time") or result_row["played_at"]),
        )

        writes = {
            "match_results": 0,
            "player_match_appearances": 0,
        }
        review_result: dict[str, Any] = {
            "status": "skipped",
            "reason": "dry_run" if dry_run else "review_callback_unavailable",
        }
        if not dry_run:
            upserted = self.repository.upsert_facts(
                {
                    "match_results": [result_row],
                    "player_match_appearances": appearances.rows,
                }
            )
            writes = {
                "match_results": int(upserted.get("match_results", 0)),
                "player_match_appearances": int(upserted.get("player_match_appearances", 0)),
            }
            if self.review_callback is not None:
                review_result = self.review_callback(fixture, result_row, prediction)
            elif prediction is None:
                review_result = {
                    "status": "skipped",
                    "reason": "pre_match_prediction_missing",
                }

        data_quality = {
            "result": "ok",
            "lineup": appearances.lineup_status,
            "player_appearances": appearances.appearance_status,
            "prediction_snapshot": "ok" if prediction else "missing",
            "post_match_review": _review_quality(review_result),
        }
        status = "ok" if all(
            data_quality[key] == "ok"
            for key in ("result", "lineup", "player_appearances")
        ) else "partial"
        if not dry_run:
            self.repository.record_audit(
                str(getattr(self.provider, "source", "post_match_provider")),
                "post_match_sync_coverage",
                fixture_id,
                "sync",
                result_row["available_at"],
                {
                    "status": status,
                    "data_quality": data_quality,
                    "appearance_count": len(appearances.rows),
                    "diagnostic_count": len(diagnostics),
                },
            )
        return {
            "fixture_id": fixture_id,
            "source_fixture_id": source_fixture_id,
            "status": status,
            "data_quality": data_quality,
            "result": result_row,
            "appearance_count": len(appearances.rows),
            "diagnostics": diagnostics,
            "prediction_id": prediction.get("prediction_id") if prediction else None,
            "review": review_result,
            "writes": writes,
            "dry_run": dry_run,
        }


def normalize_completed_match_result(
    payload: Any,
    *,
    fixture: dict[str, Any],
    source: str,
    source_fixture_id: str,
    available_at: str,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    status_payload = payload.get("sport_event_status")
    if not isinstance(status_payload, dict):
        status_payload = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    status = str(
        status_payload.get("status")
        or status_payload.get("match_status")
        or payload.get("status")
        or ""
    ).strip().lower()
    if status not in CLOSED_EVENT_STATUSES:
        return None
    home_score = _int_or_none(status_payload.get("home_score"))
    away_score = _int_or_none(status_payload.get("away_score"))
    if home_score is None or away_score is None:
        return None
    played_at = str(
        _nested(payload, "sport_event", "start_time")
        or payload.get("start_time")
        or fixture.get("match_time")
        or ""
    )
    return {
        "result_id": f"post_match_{_safe_id(source_fixture_id)}",
        "fixture_id": str(fixture["fixture_id"]),
        "home_score": home_score,
        "away_score": away_score,
        "result_status": "closed",
        "played_at": played_at,
        "available_at": available_at,
        "source": source,
        "source_result_id": f"{source_fixture_id}:result",
        "actual_outcome": (
            "home_win"
            if home_score > away_score
            else "away_win"
            if away_score > home_score
            else "draw"
        ),
        "over_2_5_result": home_score + away_score > 2,
    }


def normalize_player_match_appearances(
    *,
    repository: ResearchDatabaseRepository,
    fixture: dict[str, Any],
    summary_payload: Any,
    lineup_payload: Any,
    extended_payload: Any,
    source: str,
    source_fixture_id: str,
    available_at: str,
) -> AppearanceNormalization:
    competitors = _lineup_competitors(lineup_payload)
    if not competitors:
        return AppearanceNormalization(
            rows=[],
            diagnostics=[
                {
                    "status": "missing",
                    "scope": "lineup",
                    "reason": "lineup_competitors_missing",
                }
            ],
            lineup_status="missing",
            appearance_status="missing",
        )

    event_competitors = _event_competitor_qualifiers(summary_payload)
    minutes_by_player = _player_minutes_by_source_id(extended_payload)
    rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    lineup_player_count = 0

    for competitor in competitors:
        qualifier = str(competitor.get("qualifier") or event_competitors.get(str(competitor.get("id") or "")) or "")
        team_id = (
            str(fixture["home_team_id"])
            if qualifier == "home"
            else str(fixture["away_team_id"])
            if qualifier == "away"
            else ""
        )
        if not team_id:
            diagnostics.append(
                {
                    "status": "unmapped",
                    "scope": "team",
                    "source_team_id": competitor.get("id"),
                    "reason": "lineup_competitor_qualifier_missing",
                }
            )
            continue
        players = competitor.get("players")
        if not isinstance(players, list):
            continue
        for player in players:
            if not isinstance(player, dict):
                continue
            lineup_player_count += 1
            source_player_id = str(
                player.get("id")
                or _nested(player, "player", "id")
                or ""
            )
            player_name = str(
                player.get("name")
                or _nested(player, "player", "name")
                or ""
            )
            mapping = repository.resolve_entity(
                "player",
                name=player_name or None,
                source=source,
                source_id=source_player_id or None,
                available_at_cutoff=available_at,
            )
            player_id = str(mapping.get("entity_id") or mapping.get("player_id") or "")
            if not player_id:
                diagnostics.append(
                    {
                        "status": "unmapped",
                        "scope": "player",
                        "source_player_id": source_player_id,
                        "player_name": player_name,
                        "team_id": team_id,
                        "reason": str(mapping.get("reason") or "player_mapping_missing"),
                    }
                )
                continue
            local_player = repository.get_player(player_id) or {}
            local_team_id = str(local_player.get("team_id") or "")
            if local_team_id and local_team_id != team_id:
                diagnostics.append(
                    {
                        "status": "unmapped",
                        "scope": "player",
                        "source_player_id": source_player_id,
                        "player_name": player_name,
                        "team_id": team_id,
                        "reason": "mapped_player_team_mismatch",
                    }
                )
                continue

            starter = _bool_or_none(player.get("starter"))
            minutes = _first_int(
                player,
                (
                    "minutes_played",
                    "played_minutes",
                    "minutes",
                    "time_played",
                ),
            )
            if minutes is None and source_player_id:
                minutes = minutes_by_player.get(source_player_id)
            appeared = _bool_or_none(player.get("played"))
            if appeared is False and minutes is None:
                minutes = 0
            if appeared is None:
                if minutes is not None:
                    appeared = minutes > 0 or starter is True
                elif starter is True:
                    appeared = True
                else:
                    diagnostics.append(
                        {
                            "status": "partial",
                            "scope": "player",
                            "source_player_id": source_player_id,
                            "player_name": player_name,
                            "team_id": team_id,
                            "reason": "appearance_status_unknown_without_played_or_minutes",
                        }
                    )
                    continue
            rows.append(
                {
                    "appearance_id": f"appearance_{_safe_id(source_fixture_id)}_{_safe_id(source_player_id or player_id)}",
                    "fixture_id": str(fixture["fixture_id"]),
                    "player_id": player_id,
                    "team_id": team_id,
                    "played_at": str(
                        _nested(summary_payload, "sport_event", "start_time")
                        or fixture.get("match_time")
                        or ""
                    ),
                    "appeared": appeared,
                    "starter": starter,
                    "minutes_played": minutes,
                    "position": player.get("position") or _nested(player, "player", "position"),
                    "shirt_number": _int_or_none(
                        player.get("jersey_number")
                        or player.get("shirt_number")
                        or player.get("number")
                    ),
                    "source": source,
                    "source_appearance_id": f"{source_fixture_id}:{source_player_id or player_id}",
                    "source_fixture_id": source_fixture_id,
                    "source_player_id": source_player_id or None,
                    "available_at": available_at,
                }
            )

    if not rows:
        appearance_status = "missing"
    elif any(row.get("minutes_played") is None for row in rows) or any(
        item.get("status") == "unmapped" for item in diagnostics
    ):
        appearance_status = "partial"
    else:
        appearance_status = "ok"
    lineup_status = "ok" if lineup_player_count and not any(
        item.get("scope") == "team" for item in diagnostics
    ) else "partial"
    return AppearanceNormalization(
        rows=rows,
        diagnostics=diagnostics,
        lineup_status=lineup_status,
        appearance_status=appearance_status,
    )


def _adapter_payload(result: Any) -> tuple[Any, str | None]:
    error = getattr(result, "error", None)
    if error is not None:
        message = getattr(error, "message", None) or getattr(error, "error", None) or str(error)
        return {}, str(message)
    if hasattr(result, "data"):
        return getattr(result, "data"), None
    if isinstance(result, dict):
        return result, None
    return {}, "provider_response_invalid"


def _provider_failure_report(fixture_id: str, scope: str, reason: str) -> dict[str, Any]:
    return {
        "fixture_id": fixture_id,
        "status": "failed",
        "reason": f"provider_{scope}_failed",
        "error": {"type": "ProviderError", "message": reason},
        "data_quality": {
            "result": "missing",
            "lineup": "missing",
            "player_appearances": "missing",
            "prediction_snapshot": "missing",
            "post_match_review": "failed",
        },
        "writes": {
            "match_results": 0,
            "player_match_appearances": 0,
        },
    }


def _lineup_competitors(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    candidates: Any = payload.get("lineups")
    if isinstance(candidates, dict):
        candidates = candidates.get("competitors") or candidates.get("lineups")
    if not isinstance(candidates, list):
        candidates = payload.get("competitors")
    return [item for item in candidates or [] if isinstance(item, dict)]


def _event_competitor_qualifiers(payload: Any) -> dict[str, str]:
    competitors = _nested(payload, "sport_event", "competitors")
    if not isinstance(competitors, list):
        return {}
    return {
        str(item.get("id") or ""): str(item.get("qualifier") or "")
        for item in competitors
        if isinstance(item, dict) and item.get("id")
    }


def _player_minutes_by_source_id(payload: Any) -> dict[str, int]:
    result: dict[str, int] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            player = value.get("player") if isinstance(value.get("player"), dict) else value
            source_player_id = str(player.get("id") or "") if isinstance(player, dict) else ""
            statistics = value.get("statistics") if isinstance(value.get("statistics"), dict) else value
            minutes = _first_int(
                statistics,
                ("minutes_played", "played_minutes", "minutes", "time_played"),
            )
            if source_player_id and minutes is not None:
                result[source_player_id] = minutes
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return result


def _appearance_rows_complete(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and all(
        row.get("starter") is not None and row.get("minutes_played") is not None
        for row in rows
    )


def _coverage_audit_complete(audit: dict[str, Any] | None) -> bool:
    summary = (audit or {}).get("summary")
    if not isinstance(summary, dict):
        return False
    data_quality = summary.get("data_quality")
    return isinstance(data_quality, dict) and all(
        data_quality.get(key) == "ok"
        for key in ("result", "lineup", "player_appearances")
    )


def _review_quality(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "")
    if status in {"ok", "already_exists"}:
        return "ok"
    if status == "failed" or result.get("error"):
        return "failed"
    return "skipped"


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return _parse_datetime(str(value))
    except ValueError:
        return None


def _nested(payload: Any, *keys: str) -> Any:
    value = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_int(payload: Any, keys: tuple[str, ...]) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        parsed = _int_or_none(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return None


def _safe_id(value: str) -> str:
    return "_".join(part for part in str(value).replace(":", "_").split() if part)
