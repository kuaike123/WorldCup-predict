from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.storage.json_store import JsonStore
from app.storage.repository import LocalRepository

from .pre_match_research_scoring import PreMatchResearchScoringService
from .repository import ResearchDatabaseRepository
from .world_cup_research_backfill import (
    DEFAULT_DB_PATH,
    DEFAULT_LOCAL_DATE_UTC_OFFSET_HOURS,
    DEFAULT_LOCAL_STORE_PATH,
    DEFAULT_TARGET_BUNDLE_DIR,
    resolve_target_fixture_rows,
    run_targeted_backfill,
)


PREDICTION_SCHEMA_VERSION = "world_cup_prediction.v1"


def run_world_cup_prediction(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    output_dir: Path = DEFAULT_TARGET_BUNDLE_DIR,
    fixture_ids: list[str] | None = None,
    local_date: str | None = None,
    local_utc_offset_hours: int = DEFAULT_LOCAL_DATE_UTC_OFFSET_HOURS,
    available_at: str | None = None,
    skill_scripts_dir: Path | None = None,
    crawler_python_path: str | None = None,
    crawler_timeout_seconds: float | None = None,
    run_backfill: bool = True,
    persist: bool = False,
    local_store_path: Path = DEFAULT_LOCAL_STORE_PATH,
) -> dict[str, Any]:
    repository = ResearchDatabaseRepository(db_path)
    fixture_rows = resolve_target_fixture_rows(
        repository,
        fixture_ids=fixture_ids,
        local_date=local_date,
        local_utc_offset_hours=local_utc_offset_hours,
    )
    target_fixture_ids = [str(row["fixture_id"]) for row in fixture_rows]
    backfill_summary: dict[str, Any] | None = None
    backfill_error: str | None = None

    if run_backfill:
        try:
            backfill_summary = run_targeted_backfill(
                db_path=db_path,
                output_dir=output_dir,
                fixture_ids=target_fixture_ids,
                available_at=available_at,
                local_utc_offset_hours=local_utc_offset_hours,
                skill_scripts_dir=skill_scripts_dir,
                crawler_python_path=crawler_python_path,
                crawler_timeout_seconds=crawler_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            backfill_error = str(exc)

    local_store = LocalRepository(JsonStore(local_store_path))
    service = PreMatchResearchScoringService(
        repository,
        bundle_dir=output_dir,
        local_store_repository=local_store,
    )
    predictions = [
        _prediction_item(
            service,
            fixture,
            persist=persist,
        )
        for fixture in fixture_rows
    ]
    status = _overall_status(predictions, backfill_summary, backfill_error)
    return {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "status": status,
        "request": {
            "fixture_ids": target_fixture_ids,
            "local_date": local_date,
            "local_utc_offset_hours": local_utc_offset_hours,
            "backfill": "skipped" if not run_backfill else (backfill_summary or {}).get("status", "failed"),
        },
        "source": _source_summary(backfill_summary, run_backfill=run_backfill),
        "backfill_error": backfill_error,
        "predictions": predictions,
        "output_contract": {
            "required_per_fixture_fields": [
                "fixture_id",
                "match_time_beijing",
                "home_team",
                "away_team",
                "data_status",
                "probabilities",
                "risk",
                "coverage",
                "calibration",
                "vip",
                "sources",
                "gaps",
            ]
        },
    }


def _prediction_item(
    service: PreMatchResearchScoringService,
    fixture: dict[str, Any],
    *,
    persist: bool,
) -> dict[str, Any]:
    fixture_id = str(fixture["fixture_id"])
    try:
        prediction = (
            service.build_and_save_prediction(fixture_id)
            if persist
            else service.build_prediction(fixture_id)
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "fixture_id": fixture_id,
            "match_time_beijing": _beijing_time(str(fixture.get("match_time") or "")),
            "home_team": str(fixture.get("home_team_id") or ""),
            "away_team": str(fixture.get("away_team_id") or ""),
            "data_status": "failed",
            "probabilities": {},
            "risk": {},
            "coverage": {},
            "calibration": {},
            "vip": _empty_vip(),
            "sources": [],
            "gaps": [str(exc)],
        }

    probabilities = dict(prediction.get("probabilities") or {})
    if "over_2_5" in probabilities:
        probabilities.setdefault("under_2_5", round(1.0 - float(probabilities["over_2_5"]), 3))
    gaps = _prediction_gaps(probabilities, prediction)
    return {
        "fixture_id": fixture_id,
        "match_time_beijing": _beijing_time(str(fixture.get("match_time") or "")),
        "home_team": prediction.get("home_team") or str(fixture.get("home_team_id") or ""),
        "away_team": prediction.get("away_team") or str(fixture.get("away_team_id") or ""),
        "data_status": "partial" if gaps else "ok",
        "probabilities": probabilities,
        "risk": prediction.get("risk") or {},
        "coverage": prediction.get("coverage") or {},
        "calibration": prediction.get("calibration") or {},
        "vip": _vip_summary(probabilities, prediction),
        "sources": _sources(prediction),
        "gaps": gaps,
    }


def _prediction_gaps(probabilities: dict[str, Any], prediction: dict[str, Any]) -> list[str]:
    gaps = []
    for key in ("home_win", "draw", "away_win", "over_2_5", "under_2_5"):
        if key not in probabilities:
            gaps.append(f"missing_probability:{key}")
    for key in ("btts_yes", "btts_no"):
        if key not in probabilities:
            gaps.append(f"unsupported_probability:{key}")
    coverage_status = str((prediction.get("coverage") or {}).get("status") or "")
    if coverage_status and coverage_status != "ok":
        gaps.append(f"coverage:{coverage_status}")
    return gaps


def _vip_summary(probabilities: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    one_x_two = {
        "home_win": probabilities.get("home_win"),
        "draw": probabilities.get("draw"),
        "away_win": probabilities.get("away_win"),
    }
    main_pick = max(
        ((key, value) for key, value in one_x_two.items() if isinstance(value, int | float)),
        key=lambda item: item[1],
        default=(None, None),
    )[0]
    over = probabilities.get("over_2_5")
    secondary_pick = None
    if isinstance(over, int | float):
        secondary_pick = "over_2_5" if over >= 0.5 else "under_2_5"
    return {
        "main_pick": main_pick,
        "secondary_pick": secondary_pick,
        "scorelines": [],
        "risk_level": (prediction.get("risk") or {}).get("level"),
        "capital_allocation": None,
        "risk_reward": None,
    }


def _empty_vip() -> dict[str, Any]:
    return {
        "main_pick": None,
        "secondary_pick": None,
        "scorelines": [],
        "risk_level": None,
        "capital_allocation": None,
        "risk_reward": None,
    }


def _sources(prediction: dict[str, Any]) -> list[str]:
    fields = (prediction.get("input_summary") or {}).get("team_strength_source")
    sources = ["research_db"]
    if fields:
        sources.append(str(fields))
    return sources


def _overall_status(
    predictions: list[dict[str, Any]],
    backfill_summary: dict[str, Any] | None,
    backfill_error: str | None,
) -> str:
    if not predictions or all(item["data_status"] == "failed" for item in predictions):
        return "failed"
    if backfill_error or (backfill_summary and backfill_summary.get("status") != "ok"):
        return "partial"
    return "partial" if any(item["data_status"] != "ok" for item in predictions) else "ok"


def _source_summary(backfill_summary: dict[str, Any] | None, *, run_backfill: bool) -> dict[str, Any]:
    if not run_backfill:
        return {"research_provider": "existing_db", "odds_provider": "existing_db"}
    source = (backfill_summary or {}).get("source")
    return dict(source) if isinstance(source, dict) else {"research_provider": "unknown", "odds_provider": "unknown"}


def _beijing_time(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.astimezone(timezone(timedelta(hours=8))).isoformat()


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_TARGET_BUNDLE_DIR)
    parser.add_argument("--fixture-id", action="append", default=[])
    parser.add_argument("--local-date")
    parser.add_argument("--available-at")
    parser.add_argument("--local-utc-offset-hours", type=int, default=DEFAULT_LOCAL_DATE_UTC_OFFSET_HOURS)
    parser.add_argument("--skill-scripts-dir", type=Path)
    parser.add_argument("--crawler-python-path")
    parser.add_argument("--crawler-timeout-seconds", type=float)
    parser.add_argument("--local-store-path", type=Path, default=DEFAULT_LOCAL_STORE_PATH)
    parser.add_argument("--no-backfill", action="store_true")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()

    _emit_json(
        run_world_cup_prediction(
            db_path=args.db_path,
            output_dir=args.output_dir,
            fixture_ids=args.fixture_id or None,
            local_date=args.local_date,
            available_at=args.available_at,
            local_utc_offset_hours=args.local_utc_offset_hours,
            skill_scripts_dir=args.skill_scripts_dir,
            crawler_python_path=args.crawler_python_path,
            crawler_timeout_seconds=args.crawler_timeout_seconds,
            run_backfill=not args.no_backfill,
            persist=args.persist,
            local_store_path=args.local_store_path,
        )
    )


if __name__ == "__main__":
    main()
