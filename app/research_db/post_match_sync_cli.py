from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import load_settings

from .post_match_sync import PostMatchSyncService
from .repository import ResearchDatabaseRepository
from .sportradar_soccer import SportradarSoccerAdapter


def run_post_match_sync(
    *,
    db_path: Path,
    fixture_ids: list[str] | None = None,
    lookback_hours: int = 48,
    delay_minutes: int = 30,
    max_fixtures: int = 20,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    settings = load_settings()
    if settings.post_match_sync_provider != "sportradar_soccer":
        raise ValueError(
            f"unsupported_post_match_sync_provider:{settings.post_match_sync_provider}"
        )
    repository = ResearchDatabaseRepository(db_path)
    return PostMatchSyncService(
        repository,
        SportradarSoccerAdapter(settings),
    ).sync_recent_completed_matches(
        lookback_hours=lookback_hours,
        delay_minutes=delay_minutes,
        max_fixtures=max_fixtures,
        fixture_ids=fixture_ids,
        dry_run=dry_run,
        force=force,
    )


def build_parser() -> argparse.ArgumentParser:
    settings = load_settings()
    parser = argparse.ArgumentParser(
        description="Synchronize completed World Cup results and player appearances."
    )
    parser.add_argument("--db-path", type=Path, default=settings.research_db_path)
    parser.add_argument("--fixture-id", action="append", dest="fixture_ids")
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=settings.post_match_sync_lookback_hours,
    )
    parser.add_argument(
        "--delay-minutes",
        type=int,
        default=settings.post_match_sync_delay_minutes,
    )
    parser.add_argument(
        "--max-fixtures",
        type=int,
        default=settings.post_match_sync_max_fixtures,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_post_match_sync(
        db_path=args.db_path,
        fixture_ids=args.fixture_ids,
        lookback_hours=max(args.lookback_hours, 0),
        delay_minutes=max(args.delay_minutes, 0),
        max_fixtures=max(args.max_fixtures, 1),
        dry_run=args.dry_run,
        force=args.force,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
