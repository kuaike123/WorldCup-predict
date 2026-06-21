from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.storage.repository import LocalRepository
from app.storage.repository import utc_now
from src.scoring.field_normalizer import clamp
from src.scoring.pre_match_research_preview import P0_15_VERSION

from .features import HistoricalFeatureExtractor
from .motivation_context import build_motivation_context
from .repository import ResearchDatabaseRepository
from .world_cup_context_correction import build_world_cup_context_correction


DEFAULT_P0_11_BUNDLE_DIR = ROOT / "data" / "research_import" / "p0_11"
DEFAULT_P0_11_MANIFEST_PATH = DEFAULT_P0_11_BUNDLE_DIR / "source_manifest.json"
EXPECTED_PLAYER_FORM_SNAPSHOTS_PER_TEAM = 8
LINEUP_SOURCE_CONFIDENCE = {
    "official_confirmed_lineup": 1.0,
    "trusted_media_projected_lineup": 0.85,
    "ai_inferred_lineup": 0.60,
    "neutral_fallback": 0.0,
}


class PreMatchResearchFeatureError(ValueError):
    """Raised when P0.15 Research DB feature extraction cannot continue."""


class PreMatchResearchFeatureBuilder:
    def __init__(
        self,
        repository: ResearchDatabaseRepository,
        *,
        bundle_dir: Path = DEFAULT_P0_11_BUNDLE_DIR,
        local_store_repository: LocalRepository | None = None,
    ) -> None:
        self.repository = repository
        self.bundle_dir = bundle_dir
        self.local_store_repository = local_store_repository
        self.extractor = HistoricalFeatureExtractor(repository)
        manifest = self._manifest()
        self.player_form_target_counts = _manifest_player_form_target_counts(manifest)

    def default_fixture_ids(self) -> list[str]:
        manifest = self._manifest()
        return [
            str(fixture_id)
            for fixture_id in manifest.get("default_snapshot_fixture_ids", [])
            if str(fixture_id).strip()
        ]

    def build_feature_vector(self, fixture_id: str) -> dict[str, Any]:
        fixture = self.repository.get_fixture(fixture_id)
        if fixture is None:
            raise PreMatchResearchFeatureError(f"fixture_not_found:{fixture_id}")
        match_time = str(fixture.get("match_time") or "")
        as_of = _snapshot_cutoff(match_time)
        home_team = self.repository.get_team(str(fixture["home_team_id"]))
        away_team = self.repository.get_team(str(fixture["away_team_id"]))
        if home_team is None or away_team is None:
            raise PreMatchResearchFeatureError(f"fixture_team_not_found:{fixture_id}")
        crawler_snapshot = self._latest_pre_match_crawler_snapshot_before_match(fixture_id, match_time)
        legacy_news_snapshot = self._latest_pre_match_news_snapshot_before_match(fixture_id, match_time)
        lineup_snapshot = (
            crawler_snapshot
            if _snapshot_has_component_signal(crawler_snapshot, ("lineup_status",))
            else legacy_news_snapshot
        )
        key_player_snapshot = (
            crawler_snapshot
            if _snapshot_has_component_signal(crawler_snapshot, ("injury_status", "key_player_status"))
            else legacy_news_snapshot
        )
        motivation_snapshot = (
            crawler_snapshot
            if _snapshot_has_component_signal(crawler_snapshot, ("motivation_status",))
            else legacy_news_snapshot
        )
        evidence_snapshot = _latest_captured_snapshot(
            lineup_snapshot,
            key_player_snapshot,
            motivation_snapshot,
            legacy_news_snapshot,
        )
        if evidence_snapshot is not None and evidence_snapshot.get("captured_at"):
            as_of = _later_timestamp(as_of, str(evidence_snapshot["captured_at"]))

        home_features = self._team_features(
            str(fixture["home_team_id"]),
            match_time=match_time,
            available_at_cutoff=as_of,
        )
        away_features = self._team_features(
            str(fixture["away_team_id"]),
            match_time=match_time,
            available_at_cutoff=as_of,
        )
        self._apply_news_context(
            home_features,
            away_features,
            lineup_snapshot=lineup_snapshot,
            key_player_snapshot=key_player_snapshot,
        )
        odds = self._odds_features(fixture_id, available_at_cutoff=as_of)
        motivation_context = build_motivation_context(
            {"fixture_id": fixture_id, **fixture},
            home_team,
            away_team,
        )
        self._apply_snapshot_motivation(motivation_context, motivation_snapshot)
        world_cup_context = build_world_cup_context_correction(
            {"fixture_id": fixture_id, **fixture},
            home_team,
            away_team,
            motivation_context=motivation_context,
            snapshots=[crawler_snapshot, legacy_news_snapshot],
        )
        return {
            "version": P0_15_VERSION,
            "fixture_id": fixture_id,
            "match_id": fixture_id,
            "generated_at": utc_now(),
            "as_of": as_of,
            "weights_version": "p0.15-research-preview",
            "not_used_in_production_scoring_by_default": True,
            "match": {
                "fixture_id": fixture_id,
                "match_time": match_time,
                "competition": fixture.get("competition"),
                "season": fixture.get("season"),
                "neutral_field": bool(fixture.get("neutral_field")),
                "source": fixture.get("source"),
                "source_fixture_id": fixture.get("source_fixture_id"),
            },
            "home_team": {
                "team_id": home_team["team_id"],
                "name": home_team["canonical_name"],
                "source_team_id": home_team.get("source_team_id"),
            },
            "away_team": {
                "team_id": away_team["team_id"],
                "name": away_team["canonical_name"],
                "source_team_id": away_team.get("source_team_id"),
            },
            "team_features": {
                "home": home_features,
                "away": away_features,
            },
            "odds": odds,
            "pre_match_crawler_snapshot": crawler_snapshot or _unavailable_crawler_context(fixture_id),
            "pre_match_news_snapshot": legacy_news_snapshot or _unavailable_news_context(fixture_id),
            "motivation_context": motivation_context,
            "world_cup_context_correction": world_cup_context,
            "coverage": {
                "source": "research_db_p0_11_staging",
                "team_feature_status": {
                    "home": home_features["component_status"],
                    "away": away_features["component_status"],
                },
                "odds_status": odds["status"],
                "pre_match_crawler_status": _snapshot_overall_status(crawler_snapshot),
                "pre_match_news_status": _snapshot_overall_status(legacy_news_snapshot),
                "motivation_status": motivation_context["status"],
                "world_cup_context_status": world_cup_context["status"],
            },
        }

    def _team_features(
        self,
        team_id: str,
        *,
        match_time: str,
        available_at_cutoff: str,
    ) -> dict[str, Any]:
        historical = self.extractor.extract_team_features(
            team_id,
            match_time=match_time,
            available_at_cutoff=available_at_cutoff,
        )
        features = dict(historical.get("features", {}))
        sample_size = int(historical.get("sample_size") or 0)
        strength = self._team_strength(team_id, available_at_cutoff=available_at_cutoff)
        player_form = self._player_form(team_id, available_at_cutoff=available_at_cutoff)
        schedule_score = _rest_days_score(features.get("rest_days"))

        features.update({
            "team_id": team_id,
            "sample_size": sample_size,
            "team_strength_score": strength["score"],
            "fifa_rank_score": strength["score"],
            "key_player_form_score": player_form["score"],
            "key_player_form_summary": player_form["summary"],
            "schedule_fatigue_score": schedule_score,
            "component_status": {
                "team_strength": strength["status"],
                "recent_form": _historical_status(
                    features,
                    ("recent_points_per_game", "last_5_goal_diff", "unbeaten_rate", "friendly_match_ratio"),
                    sample_size,
                ),
                "attack_defense_efficiency": _historical_status(
                    features,
                    ("goals_for_per_match", "goals_against_per_match"),
                    sample_size,
                ),
                "schedule_fatigue": "ok" if schedule_score is not None else "unavailable",
                "key_player_status": player_form["status"],
            },
            "component_quality": {
                "team_strength": strength["quality_score"],
                "recent_form": _sample_quality(sample_size),
                "attack_defense_efficiency": _sample_quality(sample_size),
                "schedule_fatigue": 1.0 if schedule_score is not None else 0.0,
                "key_player_status": player_form["quality_score"],
            },
            "missing_reasons": {
                "team_strength": strength["missing_reason"],
                "recent_form": _historical_missing_reason(
                    features,
                    ("recent_points_per_game", "last_5_goal_diff", "unbeaten_rate", "friendly_match_ratio"),
                    sample_size,
                ),
                "attack_defense_efficiency": _historical_missing_reason(
                    features,
                    ("goals_for_per_match", "goals_against_per_match"),
                    sample_size,
                ),
                "schedule_fatigue": None
                if schedule_score is not None
                else "rest_days_unavailable",
                "key_player_status": player_form["missing_reason"],
            },
            "source_audit": historical.get("source_audit", []),
            "blocked_by_available_at": historical.get("blocked_by_available_at", []),
            "team_strength_summary": strength["summary"],
        })
        return features

    def _team_strength(self, team_id: str, *, available_at_cutoff: str) -> dict[str, Any]:
        snapshots = [
            item
            for item in self.repository.list_team_strength_snapshots(team_id)
            if _available_at_or_before(item.get("available_at"), available_at_cutoff)
        ]
        strength_snapshot = next(
            (
                item
                for item in snapshots
                if str(item.get("strength_type")) == "fifa_world_ranking_position"
            ),
            None,
        )
        if strength_snapshot is None:
            strength_snapshot = next(
                (
                    item
                    for item in snapshots
                    if str(item.get("strength_type")) == "world_football_elo_rank"
                ),
                None,
            )
        if strength_snapshot is None:
            return {
                "score": None,
                "status": "unavailable",
                "quality_score": 0.0,
                "missing_reason": "team_strength_snapshot_missing",
                "summary": {"snapshots_used": 0},
            }
        rank = float(strength_snapshot["strength_value"])
        return {
            "score": _fifa_rank_to_score(rank),
            "status": "ok",
            "quality_score": 1.0 if str(strength_snapshot.get("strength_type")) == "fifa_world_ranking_position" else 0.85,
            "missing_reason": None,
            "summary": {
                "snapshots_used": 1,
                "strength_type": strength_snapshot.get("strength_type"),
                "strength_value": rank,
                "strength_source": strength_snapshot.get("strength_source"),
                "as_of": strength_snapshot.get("as_of"),
            },
        }

    def _player_form(self, team_id: str, *, available_at_cutoff: str) -> dict[str, Any]:
        expected_snapshots = self._expected_player_form_snapshots(team_id)
        rows = [
            item
            for item in self.repository.list_player_form_snapshots(team_id=team_id)
            if _available_at_or_before(item.get("available_at"), available_at_cutoff)
        ]
        if not rows:
            return {
                "score": None,
                "status": "unavailable",
                "quality_score": 0.0,
                "missing_reason": "player_form_snapshots_missing",
                "summary": {
                    "snapshots_used": 0,
                    "expected_snapshots": expected_snapshots,
                    "club_mapped_rows": 0,
                },
            }
        team_labels = _team_labels(self.repository.get_team(team_id))
        player_scores = [_player_form_score(row, team_labels=team_labels) for row in rows]
        score = round(sum(player_scores) / len(player_scores), 2)
        club_mapped_rows = sum(1 for row in rows if _has_distinct_club_name(row, team_labels))
        row_coverage = min(1.0, len(rows) / expected_snapshots)
        club_mapping_quality = club_mapped_rows / len(rows)
        quality = round(row_coverage * (0.7 + 0.3 * club_mapping_quality), 3)
        missing_reason = None
        status = "ok"
        if len(rows) < expected_snapshots:
            status = "partial"
            missing_reason = "player_form_snapshot_count_below_expected"
        elif club_mapped_rows < len(rows):
            status = "partial"
            missing_reason = "player_form_proxy_partial_club_mapping"
        return {
            "score": score,
            "status": status,
            "quality_score": quality,
            "missing_reason": missing_reason,
            "summary": {
                "snapshots_used": len(rows),
                "expected_snapshots": expected_snapshots,
                "club_mapped_rows": club_mapped_rows,
                "average_score": score,
                "source": "derived_from_player_form_snapshots",
            },
        }

    def _expected_player_form_snapshots(self, team_id: str) -> int:
        expected = int(self.player_form_target_counts.get(team_id, EXPECTED_PLAYER_FORM_SNAPSHOTS_PER_TEAM))
        return max(expected, 1)

    def _odds_features(self, fixture_id: str, *, available_at_cutoff: str) -> dict[str, Any]:
        odds_rows = self._live_odds_rows(fixture_id, available_at_cutoff=available_at_cutoff)
        diagnostics = self._odds_diagnostics_by_fixture().get(fixture_id, {})
        using_live_odds = bool(odds_rows)
        if not odds_rows:
            odds_rows = [
            row
            for row in self._odds_rows()
            if isinstance(row, dict) and str(row.get("fixture_id")) == fixture_id
            and _available_at_or_before(row.get("available_at"), available_at_cutoff)
            ]
        if not odds_rows:
            return {
                "status": "unavailable",
                "quality_score": 0.0,
                "home_score": 50.0,
                "away_score": 50.0,
                "total_goals_line": None,
                "source_fields": [],
                "snapshots_count": 0,
                "markets": [],
                "missing_reason": str(
                    diagnostics.get("status")
                    or "odds_snapshots_unavailable_for_fixture"
                ),
                "diagnostic": diagnostics,
            }
        markets = sorted({str(row.get("market_type")) for row in odds_rows})
        side_scores = _odds_side_scores(odds_rows)
        side_signal_count = int(side_scores.get("side_signal_count") or 0)
        has_total_line = side_scores.get("total_goals_line") is not None
        if side_signal_count == 0:
            status = "partial" if has_total_line else "unavailable"
            missing_reason = "odds_side_price_signal_unavailable"
        elif set(markets) == {"h2h", "spreads", "totals"} and side_signal_count >= 2:
            status = "ok"
            missing_reason = None
        else:
            status = "partial"
            missing_reason = "odds_market_or_side_signal_coverage_partial"
        market_quality = min(1.0, len(markets) / 3)
        side_signal_quality = min(1.0, side_signal_count / 2)
        return {
            "status": status,
            "quality_score": round(market_quality * side_signal_quality, 3),
            "home_score": side_scores["home_score"],
            "away_score": side_scores["away_score"],
            "total_goals_line": side_scores.get("total_goals_line"),
            "source_fields": [f"odds_snapshots.{market}" for market in markets],
            "snapshots_count": len(odds_rows),
            "markets": markets,
            "side_signal_count": side_signal_count,
            "missing_reason": missing_reason,
            "diagnostic": {
                **diagnostics,
                "source": "local_store_latest_odds" if using_live_odds else "p0_11_bundle",
            },
        }

    def _manifest(self) -> dict[str, Any]:
        return _read_json(self.bundle_dir / "source_manifest.json", default={})

    def _odds_rows(self) -> list[dict[str, Any]]:
        rows = _read_json(self.bundle_dir / "odds_snapshots.json", default=[])
        return rows if isinstance(rows, list) else []

    def _odds_diagnostics_by_fixture(self) -> dict[str, dict[str, Any]]:
        payload = _read_json(self.bundle_dir / "odds_diagnostics.json", default={})
        diagnostics = payload.get("diagnostics", []) if isinstance(payload, dict) else []
        return {
            str(item.get("fixture_id")): item
            for item in diagnostics
            if isinstance(item, dict) and item.get("fixture_id")
        }

    def _live_odds_rows(self, fixture_id: str, *, available_at_cutoff: str) -> list[dict[str, Any]]:
        if self.local_store_repository is None:
            return []
        snapshots = [
            item
            for item in self.local_store_repository.list_odds_snapshots(fixture_id)
            if _available_at_or_before(
                item.get("captured_at") or item.get("available_at"),
                available_at_cutoff,
            )
        ]
        if not snapshots:
            return []
        latest_captured_at = max(str(item.get("captured_at") or "") for item in snapshots)
        latest_rows = [
            item
            for item in snapshots
            if str(item.get("captured_at") or "") == latest_captured_at
        ]
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in latest_rows:
            market_type = str(item.get("market_type") or "")
            bookmaker = str(item.get("bookmaker") or item.get("bookmaker_name") or "latest")
            key = (bookmaker, market_type)
            row = grouped.setdefault(
                key,
                {
                    "fixture_id": fixture_id,
                    "match_id": fixture_id,
                    "captured_at": latest_captured_at,
                    "available_at": latest_captured_at,
                    "source": item.get("source") or "local_store_latest_odds",
                    "source_event_id": item.get("source_event_id") or item.get("external_event_id"),
                    "source_game_url": item.get("source_game_url"),
                    "source_page_url": item.get("source_page_url"),
                    "bookmaker_id": item.get("bookmaker_id"),
                    "bookmaker_name": bookmaker,
                    "bookmakers_count": 1,
                    "snapshot_id": f"live_odds_{fixture_id}_{bookmaker}_{market_type}",
                    "market_type": market_type,
                    "home_odds": None,
                    "draw_odds": None,
                    "away_odds": None,
                    "spread_line": None,
                    "home_spread_line": None,
                    "away_spread_line": None,
                    "home_water": None,
                    "away_water": None,
                    "total_goals_line": None,
                    "over_water": None,
                    "under_water": None,
                    "status": "ok",
                    "missing_fields": [],
                },
            )
            selection = str(item.get("selection") or "")
            odds_decimal = item.get("odds_decimal")
            if market_type == "h2h":
                if selection == "home":
                    row["home_odds"] = odds_decimal
                elif selection == "draw":
                    row["draw_odds"] = odds_decimal
                elif selection == "away":
                    row["away_odds"] = odds_decimal
            elif market_type == "spreads":
                if selection == "home":
                    row["spread_line"] = item.get("line")
                    row["home_spread_line"] = item.get("line")
                    row["home_water"] = odds_decimal
                elif selection == "away":
                    row["away_spread_line"] = item.get("line")
                    row["away_water"] = odds_decimal
            elif market_type == "totals":
                row["total_goals_line"] = item.get("line")
                if selection == "over":
                    row["over_water"] = odds_decimal
                elif selection == "under":
                    row["under_water"] = odds_decimal
        return sorted(grouped.values(), key=lambda item: (str(item.get("captured_at") or ""), str(item.get("market_type") or "")))

    def _latest_pre_match_news_snapshot_before_match(
        self,
        fixture_id: str,
        match_time: str,
    ) -> dict[str, Any] | None:
        if self.local_store_repository is None:
            return None
        snapshots = self.local_store_repository.list_pre_match_news_snapshots(fixture_id=fixture_id)
        if not snapshots:
            return None
        match_dt = _parse_datetime(match_time)
        eligible = []
        for snapshot in snapshots:
            captured_at = _parse_datetime(str(snapshot.get("captured_at") or ""))
            if captured_at is None:
                continue
            if match_dt is not None and captured_at > match_dt:
                continue
            eligible.append(snapshot)
        return eligible[-1] if eligible else None

    def _latest_pre_match_crawler_snapshot_before_match(
        self,
        fixture_id: str,
        match_time: str,
    ) -> dict[str, Any] | None:
        if self.local_store_repository is None:
            return None
        snapshots = self.local_store_repository.list_pre_match_crawler_snapshots(fixture_id=fixture_id)
        if not snapshots:
            return None
        match_dt = _parse_datetime(match_time)
        eligible = []
        for snapshot in snapshots:
            captured_at = _parse_datetime(str(snapshot.get("captured_at") or ""))
            if captured_at is None:
                continue
            if match_dt is not None and captured_at > match_dt:
                continue
            if not _valid_pre_match_crawler_snapshot(snapshot):
                continue
            eligible.append(snapshot)
        return eligible[-1] if eligible else None

    @staticmethod
    def _apply_news_context(
        home_features: dict[str, Any],
        away_features: dict[str, Any],
        *,
        lineup_snapshot: dict[str, Any] | None,
        key_player_snapshot: dict[str, Any] | None,
    ) -> None:
        if lineup_snapshot is None:
            for side, features in (("home", home_features), ("away", away_features)):
                lineup_context = _neutral_lineup_integrity_context(
                    snapshot_id=None,
                    snapshot_source="pre_match_news_snapshot",
                    status="unavailable",
                    missing_reason="pre_match_news_snapshot_missing",
                    side=side,
                )
                _apply_lineup_integrity_context(features, lineup_context)
        else:
            source_quality = _snapshot_quality(lineup_snapshot)
            snapshot_source = _snapshot_source_name(lineup_snapshot)
            lineup_status = _component_status_from_snapshot(
                lineup_snapshot,
                ("lineup_status", "squad_status"),
                default="unavailable",
            )
            lineup_contexts = {
                "home": _build_lineup_integrity_context(
                    lineup_snapshot,
                    side="home",
                    snapshot_source=snapshot_source,
                    snapshot_quality=source_quality,
                    snapshot_status=lineup_status,
                ),
                "away": _build_lineup_integrity_context(
                    lineup_snapshot,
                    side="away",
                    snapshot_source=snapshot_source,
                    snapshot_quality=source_quality,
                    snapshot_status=lineup_status,
                ),
            }
            for side, features in (("home", home_features), ("away", away_features)):
                lineup_context = lineup_contexts[side]
                _apply_lineup_integrity_context(features, lineup_context)
                features["lineup_news_context"] = {
                    "snapshot_id": lineup_snapshot.get("snapshot_id"),
                    "status": lineup_context["status"],
                    "snapshot_status": lineup_status,
                    "quality_score": lineup_context["quality_score"],
                    "source": snapshot_source,
                    "source_tier": lineup_context["source_tier"],
                    "source_confidence": lineup_context["source_confidence"],
                    "raw_score": lineup_context["raw_score"],
                    "final_score": lineup_context["final_score"],
                    "evidence_count": lineup_context["evidence_count"],
                    "availability_signal_count": lineup_context["availability_signal_count"],
                    "missing_reason": lineup_context["missing_reason"],
                }

        if key_player_snapshot is None:
            return
        key_player_news_status = _component_status_from_snapshot(
            key_player_snapshot,
            ("injury_status", "key_player_status"),
            default="unavailable",
        )
        if key_player_news_status not in {"ok", "partial"}:
            return
        key_player_quality = _snapshot_quality(key_player_snapshot)
        key_player_source = _snapshot_source_name(key_player_snapshot)
        for features in (home_features, away_features):
            current_status = str(features["component_status"].get("key_player_status") or "unavailable")
            features["component_status"]["key_player_status"] = _merge_news_status(
                current_status,
                key_player_news_status,
            )
            current_quality = float(features["component_quality"].get("key_player_status") or 0.0)
            features["component_quality"]["key_player_status"] = round(
                max(current_quality, key_player_quality),
                3,
            )
            features["key_player_news_context"] = {
                "snapshot_id": key_player_snapshot.get("snapshot_id"),
                "status": key_player_news_status,
                "quality_score": key_player_quality,
                "source": key_player_source,
            }

    @staticmethod
    def _apply_snapshot_motivation(
        motivation_context: dict[str, Any],
        snapshot: dict[str, Any] | None,
    ) -> None:
        if snapshot is None:
            return
        status = _component_status_from_snapshot(
            snapshot,
            ("motivation_status",),
            default="unavailable",
        )
        if status not in {"ok", "partial"}:
            return
        source_quality = _snapshot_quality(snapshot)
        motivation_context["status"] = _merge_news_status(
            str(motivation_context.get("status") or "unavailable"),
            status,
        )
        motivation_context["quality_score"] = round(
            max(float(motivation_context.get("quality_score") or 0.0), source_quality),
            3,
        )
        reason_codes = motivation_context.setdefault("reason_codes", [])
        if isinstance(reason_codes, list) and "crawler_motivation_context" not in reason_codes:
            reason_codes.append("crawler_motivation_context")
        motivation_context["crawler_snapshot_context"] = {
            "snapshot_id": snapshot.get("snapshot_id"),
            "status": status,
            "quality_score": source_quality,
            "source": _snapshot_source_name(snapshot),
        }


def _apply_lineup_integrity_context(
    features: dict[str, Any],
    lineup_context: dict[str, Any],
) -> None:
    features["component_status"]["lineup_integrity"] = lineup_context["status"]
    features["component_quality"]["lineup_integrity"] = lineup_context["quality_score"]
    features["missing_reasons"]["lineup_integrity"] = lineup_context["missing_reason"]
    features["lineup_integrity_source_tier"] = lineup_context["source_tier"]
    features["lineup_integrity_source_confidence"] = lineup_context["source_confidence"]
    features["lineup_integrity_raw_score"] = lineup_context["raw_score"]
    features["lineup_integrity_score"] = lineup_context["final_score"]


def _build_lineup_integrity_context(
    snapshot: dict[str, Any],
    *,
    side: str,
    snapshot_source: str,
    snapshot_quality: float,
    snapshot_status: str,
) -> dict[str, Any]:
    items = snapshot.get("items") if isinstance(snapshot.get("items"), list) else []
    lineup_items = [
        item
        for item in items
        if isinstance(item, dict)
        and _lineup_item_matches_side(item, side)
        and _item_targets_component(item, "lineup_integrity")
    ]
    availability_items = [
        item
        for item in items
        if isinstance(item, dict)
        and _lineup_item_matches_side(item, side)
        and _item_targets_component(item, "key_player_status")
    ]
    if not lineup_items:
        return _neutral_lineup_integrity_context(
            snapshot_id=snapshot.get("snapshot_id"),
            snapshot_source=snapshot_source,
            status="unavailable" if snapshot_status == "unavailable" else "partial",
            missing_reason="lineup_side_evidence_missing",
            side=side,
        )

    source_tier = _lineup_source_tier(snapshot, lineup_items)
    signal_quality = _lineup_signal_quality(lineup_items, snapshot_quality=snapshot_quality)
    source_confidence = LINEUP_SOURCE_CONFIDENCE[source_tier]
    if source_tier == "ai_inferred_lineup" and signal_quality < 0.65:
        return {
            **_neutral_lineup_integrity_context(
                snapshot_id=snapshot.get("snapshot_id"),
                snapshot_source=snapshot_source,
                status="partial",
                missing_reason="ai_lineup_evidence_too_weak",
                side=side,
            ),
            "source_tier": source_tier,
            "source_confidence": source_confidence,
            "quality_score": signal_quality,
            "evidence_count": len(lineup_items),
            "availability_signal_count": len(availability_items),
        }

    raw_score = _lineup_raw_score(
        lineup_items,
        availability_items,
        signal_quality=signal_quality,
    )
    final_score = round(50 + (raw_score - 50) * source_confidence, 2)
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_source": snapshot_source,
        "side": side,
        "status": "ok" if source_tier == "official_confirmed_lineup" else "partial",
        "quality_score": signal_quality,
        "source_tier": source_tier,
        "source_confidence": source_confidence,
        "raw_score": raw_score,
        "final_score": final_score,
        "missing_reason": None,
        "evidence_count": len(lineup_items),
        "availability_signal_count": len(availability_items),
    }


def _neutral_lineup_integrity_context(
    *,
    snapshot_id: Any,
    snapshot_source: str,
    status: str,
    missing_reason: str,
    side: str,
) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "snapshot_source": snapshot_source,
        "side": side,
        "status": status,
        "quality_score": 0.0,
        "source_tier": "neutral_fallback",
        "source_confidence": 0.0,
        "raw_score": 50.0,
        "final_score": 50.0,
        "missing_reason": missing_reason,
        "evidence_count": 0,
        "availability_signal_count": 0,
    }


def _lineup_item_matches_side(item: dict[str, Any], side: str) -> bool:
    item_side = str(item.get("team_side") or item.get("team") or "").casefold()
    return item_side == side


def _item_targets_component(item: dict[str, Any], component: str) -> bool:
    targets = item.get("component_targets")
    if isinstance(targets, list) and component in {str(target) for target in targets}:
        return True
    category = str(item.get("category") or "").casefold()
    if component == "lineup_integrity":
        return "lineup" in category or "squad" in category
    if component == "key_player_status":
        return any(token in category for token in ("injury", "key_player", "missing", "suspended"))
    return False


def _lineup_source_tier(snapshot: dict[str, Any], lineup_items: list[dict[str, Any]]) -> str:
    if any(_confirmed_lineup_item(item) for item in lineup_items) and _lineup_items_from_official_source(
        snapshot,
        lineup_items,
    ):
        return "official_confirmed_lineup"
    if _lineup_items_from_ai_source(snapshot, lineup_items):
        return "ai_inferred_lineup"
    if _lineup_items_from_trusted_source(snapshot, lineup_items):
        return "trusted_media_projected_lineup"
    return "ai_inferred_lineup"


def _lineup_items_from_official_source(
    snapshot: dict[str, Any],
    lineup_items: list[dict[str, Any]],
) -> bool:
    for marker in _lineup_source_markers(snapshot, lineup_items):
        if marker in {"official_site", "team_site", "club_site"}:
            return True
        if any(token in marker for token in ("fifa", "federation", "association")):
            return True
    return False


def _lineup_items_from_trusted_source(
    snapshot: dict[str, Any],
    lineup_items: list[dict[str, Any]],
) -> bool:
    markers = _lineup_source_markers(snapshot, lineup_items)
    if any(marker in {"ai_model", "ai_generated", "ai_inferred", "llm"} for marker in markers):
        return False
    return any(
        marker in {"trusted_media", "media_or_official"}
        or any(token in marker for token in ("media", "news", "reporter", "press"))
        for marker in markers
    )


def _lineup_items_from_ai_source(
    snapshot: dict[str, Any],
    lineup_items: list[dict[str, Any]],
) -> bool:
    markers = _lineup_source_markers(snapshot, lineup_items)
    return any(marker in {"ai_model", "ai_generated", "ai_inferred", "llm"} for marker in markers)


def _lineup_source_markers(
    snapshot: dict[str, Any],
    lineup_items: list[dict[str, Any]],
) -> set[str]:
    markers: set[str] = set()
    for item in lineup_items:
        for key in ("source_type", "source"):
            value = str(item.get(key) or "").strip().casefold()
            if value:
                markers.add(value)
        actor = item.get("published_by_actor")
        if isinstance(actor, dict):
            for value in actor.values():
                text = str(value or "").strip().casefold()
                if text:
                    markers.add(text)
    source_summary = snapshot.get("source_summary")
    if isinstance(source_summary, dict):
        for key in ("adapter_source", "source"):
            value = str(source_summary.get(key) or "").strip().casefold()
            if value:
                markers.add(value)
    sources = snapshot.get("sources")
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in ("source", "source_type"):
                value = str(source.get(key) or "").strip().casefold()
                if value:
                    markers.add(value)
    return markers


def _lineup_signal_quality(
    lineup_items: list[dict[str, Any]],
    *,
    snapshot_quality: float,
) -> float:
    confidences = [float(item.get("confidence") or 0.0) for item in lineup_items]
    if not confidences:
        return round(snapshot_quality, 3)
    return round(max(snapshot_quality * 0.6, sum(confidences) / len(confidences)), 3)


def _lineup_raw_score(
    lineup_items: list[dict[str, Any]],
    availability_items: list[dict[str, Any]],
    *,
    signal_quality: float,
) -> float:
    raw_score = 50.0
    if any(_confirmed_lineup_item(item) for item in lineup_items):
        raw_score += 14.0
    elif any(_projected_lineup_item(item) for item in lineup_items):
        raw_score += 8.0
    else:
        raw_score += 5.0
    raw_score += (signal_quality - 0.55) * 18
    raw_score += _availability_signal_adjustment(availability_items)
    return round(clamp(raw_score, 35.0, 78.0), 2)


def _confirmed_lineup_item(item: dict[str, Any]) -> bool:
    category = str(item.get("category") or "").casefold()
    summary = str(item.get("summary") or "").casefold()
    return "confirmed_lineup" in category or "confirmed lineup" in summary or "starting xi" in summary


def _projected_lineup_item(item: dict[str, Any]) -> bool:
    category = str(item.get("category") or "").casefold()
    return any(token in category for token in ("projected_lineup", "lineup_hint", "squad"))


def _availability_signal_adjustment(items: list[dict[str, Any]]) -> float:
    negative_tokens = ("out", "doubtful", "suspended", "miss", "absence", "injury", "late fitness")
    positive_tokens = ("fit", "available", "returns", "full squad", "cleared")
    adjustment = 0.0
    for item in items:
        summary = str(item.get("summary") or "").casefold()
        confidence = max(float(item.get("confidence") or 0.0), 0.5)
        if any(token in summary for token in negative_tokens):
            adjustment -= 6.0 * confidence
        elif any(token in summary for token in positive_tokens):
            adjustment += 3.0 * confidence
    return clamp(adjustment, -12.0, 6.0)


def _fifa_rank_to_score(rank: float) -> float:
    # Rank 1 maps to 100; rank 100 and weaker bottom out at 20.
    return round(clamp(100 - max(rank - 1, 0) * (80 / 99), 20, 100), 2)


def _player_form_score(
    row: dict[str, Any],
    *,
    team_labels: set[str] | None = None,
) -> float:
    has_distinct_club_name = _has_distinct_club_name(row, team_labels)
    club_minutes_score = _ratio_score(row.get("club_recent_minutes"), 450) if has_distinct_club_name else 0.0
    club_start_score = _ratio_score(row.get("club_recent_starts"), 5) if has_distinct_club_name else 0.0
    national_minutes_score = _ratio_score(row.get("national_recent_minutes"), 900)
    national_start_score = _ratio_score(row.get("national_recent_starts"), 10)
    score = (
        club_minutes_score * 0.35
        + club_start_score * 0.20
        + national_minutes_score * 0.30
        + national_start_score * 0.15
    )
    return round(clamp(score), 2)


def _ratio_score(value: Any, denominator: float) -> float:
    if value is None:
        return 0.0
    return clamp(float(value) / denominator * 100)


def _has_distinct_club_name(
    row: dict[str, Any],
    team_labels: set[str] | None = None,
) -> bool:
    club_name = _normalized_label(row.get("club_name"))
    if not club_name:
        return False
    return club_name not in (team_labels or set())


def _team_labels(team: dict[str, Any] | None) -> set[str]:
    if not isinstance(team, dict):
        return set()
    return {
        label
        for label in (
            _normalized_label(team.get("canonical_name")),
            _normalized_label(team.get("country_code")),
            _normalized_label(team.get("fifa_code")),
        )
        if label
    }


def _normalized_label(value: Any) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _rest_days_score(rest_days: Any) -> float | None:
    if rest_days is None:
        return None
    return round(clamp(45 + min(float(rest_days), 7) * 7), 2)


def _manifest_player_form_target_counts(manifest: dict[str, Any]) -> dict[str, int]:
    payload = manifest.get("player_form_target_counts") if isinstance(manifest, dict) else {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, int] = {}
    for team_id, value in payload.items():
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            result[str(team_id)] = parsed
    return result


def _historical_status(
    features: dict[str, Any],
    fields: tuple[str, ...],
    sample_size: int,
) -> str:
    available = [field for field in fields if features.get(field) is not None]
    if not available:
        return "unavailable"
    if len(available) < len(fields) or sample_size < 3:
        return "partial"
    return "ok"


def _historical_missing_reason(
    features: dict[str, Any],
    fields: tuple[str, ...],
    sample_size: int,
) -> str | None:
    missing = [field for field in fields if features.get(field) is None]
    reasons = []
    if missing:
        reasons.append("missing_fields:" + ",".join(missing))
    if sample_size < 3:
        reasons.append(f"low_recent_match_sample:{sample_size}")
    return "; ".join(reasons) or None


def _sample_quality(sample_size: int) -> float:
    return round(min(1.0, sample_size / 5), 3)


def _odds_side_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    home_scores: list[float] = []
    away_scores: list[float] = []
    total_goals_line = None
    for row in rows:
        market = str(row.get("market_type") or "")
        if market == "h2h":
            h2h = _h2h_scores(row)
            if h2h:
                home_scores.append(h2h["home_score"])
                away_scores.append(h2h["away_score"])
        elif market == "spreads":
            spread = _spread_scores(row)
            if spread:
                home_scores.append(spread["home_score"])
                away_scores.append(spread["away_score"])
        elif market == "totals" and row.get("total_goals_line") is not None:
            total_goals_line = float(row["total_goals_line"])
    if not home_scores:
        return {
            "home_score": 50.0,
            "away_score": 50.0,
            "total_goals_line": total_goals_line,
            "side_signal_count": 0,
        }
    home = round(sum(home_scores) / len(home_scores), 2)
    away = round(sum(away_scores) / len(away_scores), 2)
    return {
        "home_score": home,
        "away_score": away,
        "total_goals_line": total_goals_line,
        "side_signal_count": len(home_scores),
    }


def _h2h_scores(row: dict[str, Any]) -> dict[str, float] | None:
    required = (row.get("home_odds"), row.get("draw_odds"), row.get("away_odds"))
    if any(value in (None, "", 0) for value in required):
        return None
    home_implied = 1 / float(row["home_odds"])
    draw_implied = 1 / float(row["draw_odds"])
    away_implied = 1 / float(row["away_odds"])
    total = home_implied + draw_implied + away_implied
    if total <= 0:
        return None
    home_probability = home_implied / total
    away_probability = away_implied / total
    home_score = clamp(50 + (home_probability - away_probability) * 100)
    away_score = clamp(50 + (away_probability - home_probability) * 100)
    return {"home_score": round(home_score, 2), "away_score": round(away_score, 2)}


def _spread_scores(row: dict[str, Any]) -> dict[str, float] | None:
    line = _home_spread_line(row)
    if line is None:
        return None
    water_adjustment = 0.0
    if row.get("home_water") is not None and row.get("away_water") is not None:
        water_adjustment = (float(row["away_water"]) - float(row["home_water"])) * 8
    home_score = clamp(50 - line * 18 + water_adjustment)
    away_score = clamp(100 - home_score)
    return {"home_score": round(home_score, 2), "away_score": round(away_score, 2)}


def _home_spread_line(row: dict[str, Any]) -> float | None:
    if row.get("home_spread_line") is not None:
        return float(row["home_spread_line"])
    if row.get("away_spread_line") is not None:
        return -float(row["away_spread_line"])
    if row.get("spread_line") is not None:
        return float(row["spread_line"])
    return None


def _snapshot_cutoff(match_time: str) -> str:
    match_dt = _parse_datetime(match_time)
    if match_dt is None:
        return match_time
    return (match_dt - timedelta(hours=3)).isoformat()


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _later_timestamp(current: str, candidate: str) -> str:
    current_dt = _parse_datetime(current)
    candidate_dt = _parse_datetime(candidate)
    if current_dt is None:
        return candidate if candidate_dt is not None else current
    if candidate_dt is None:
        return current
    return candidate if candidate_dt > current_dt else current


def _available_at_or_before(available_at: Any, cutoff: str) -> bool:
    available_dt = _parse_datetime(str(available_at or ""))
    cutoff_dt = _parse_datetime(cutoff)
    if available_dt is None or cutoff_dt is None:
        return False
    return available_dt <= cutoff_dt


def _unavailable_news_context(fixture_id: str) -> dict[str, Any]:
    return {
        "fixture_id": fixture_id,
        "match_id": fixture_id,
        "status": "unavailable",
        "lineup_status": "unavailable",
        "squad_status": "unavailable",
        "injury_status": "unavailable",
        "key_player_status": "unavailable",
        "motivation_status": "unavailable",
        "items": [],
        "sources": [],
        "missing_reason": "pre_match_news_snapshot_missing",
    }


def _unavailable_crawler_context(fixture_id: str) -> dict[str, Any]:
    return {
        "schema_version": "pre_match_crawler_snapshot.v1",
        "fixture_id": fixture_id,
        "match_id": fixture_id,
        "status": "unavailable",
        "lineup_status": "unavailable",
        "injury_status": "unavailable",
        "key_player_status": "unavailable",
        "motivation_status": "unavailable",
        "quality_score": 0.0,
        "items": [],
        "source_summary": {
            "sources_attempted": 0,
            "sources_succeeded": 0,
            "raw_saved": False,
        },
        "missing_reason": "pre_match_crawler_snapshot_missing",
    }


def _snapshot_overall_status(snapshot: dict[str, Any] | None) -> str:
    if snapshot is None:
        return "unavailable"
    statuses = [
        str(snapshot.get(key) or "unavailable")
        for key in (
            "lineup_status",
            "squad_status",
            "injury_status",
            "key_player_status",
            "motivation_status",
        )
    ]
    if "blocked" in statuses:
        return "blocked"
    if "ok" in statuses:
        return "ok"
    if "partial" in statuses:
        return "partial"
    if "stale" in statuses:
        return "stale"
    return "unavailable"


def _snapshot_quality(snapshot: dict[str, Any]) -> float:
    if isinstance(snapshot.get("quality_score"), (int, float)):
        return round(max(0.0, min(float(snapshot["quality_score"]), 1.0)), 3)
    sources = snapshot.get("sources") if isinstance(snapshot.get("sources"), list) else []
    quality_values = [
        float(source.get("quality_score") or 0.0)
        for source in sources
        if isinstance(source, dict)
    ]
    if quality_values:
        return round(max(0.0, min(max(quality_values), 1.0)), 3)
    return 0.0


def _snapshot_source_name(snapshot: dict[str, Any]) -> str:
    if str(snapshot.get("schema_version") or "") == "pre_match_crawler_snapshot.v1":
        return "pre_match_crawler_snapshot"
    if str(snapshot.get("snapshot_id") or "").startswith("pmcrawl_"):
        return "pre_match_crawler_snapshot"
    return "pre_match_news_snapshot"


def _latest_captured_snapshot(*snapshots: dict[str, Any] | None) -> dict[str, Any] | None:
    captured = [
        snapshot
        for snapshot in snapshots
        if snapshot is not None and _parse_datetime(str(snapshot.get("captured_at") or "")) is not None
    ]
    if not captured:
        return None
    return max(captured, key=lambda item: _parse_datetime(str(item.get("captured_at") or "")) or datetime.min)


def _snapshot_has_component_signal(snapshot: dict[str, Any] | None, keys: tuple[str, ...]) -> bool:
    if snapshot is None or snapshot.get("available_at_cutoff") is False:
        return False
    if str(snapshot.get("capture_window") or "") in {"out_of_pre_match_window", "after_cutoff"}:
        return False
    return _component_status_from_snapshot(snapshot, keys, default="unavailable") in {"ok", "partial"}


def _valid_pre_match_crawler_snapshot(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("available_at_cutoff") is False:
        return False
    if str(snapshot.get("capture_window") or "") in {"out_of_pre_match_window", "after_cutoff"}:
        return False
    statuses = [
        str(snapshot.get(key) or "unavailable")
        for key in (
            "lineup_status",
            "injury_status",
            "key_player_status",
            "motivation_status",
        )
    ]
    return any(status in {"ok", "partial"} for status in statuses)


def _component_status_from_snapshot(
    snapshot: dict[str, Any],
    keys: tuple[str, ...],
    *,
    default: str,
) -> str:
    statuses = [str(snapshot.get(key) or default) for key in keys]
    if "blocked" in statuses:
        return "blocked"
    if "ok" in statuses:
        return "ok"
    if "partial" in statuses:
        return "partial"
    if "stale" in statuses:
        return "stale"
    return default


def _merge_news_status(current: str, news_status: str) -> str:
    statuses = [current, news_status]
    if "blocked" in statuses:
        return "blocked"
    if "unavailable" in statuses:
        return "partial"
    if "partial" in statuses:
        return "partial"
    return "ok"


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
