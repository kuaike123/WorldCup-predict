from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.storage.repository import utc_now


P0_TABLES = (
    "teams",
    "players",
    "fixtures",
    "match_results",
    "squads",
    "player_stats",
    "player_form_snapshots",
    "team_strength_snapshots",
    "team_aliases",
    "feature_snapshots",
    "data_source_audit",
)

ADVANCED_METRICS_TABLES = (
    "post_match_advanced_events",
    "post_match_advanced_metric_summaries",
)
SAFE_ADVANCED_QUALIFIER_KEYS = {
    "card_type",
    "description_flags",
    "goal_event_id",
    "in_box",
    "is_big_chance",
    "is_big_chance_text",
    "is_direct_free_kick",
    "is_header",
    "is_key_pass",
    "is_one_on_one",
    "is_penalty",
    "is_shot_assist",
    "is_six_yard_box",
    "outcome",
    "recipient_player_id",
    "shot_class",
    "shot_event_id",
    "shot_location",
    "shot_method",
    "simplified_xg",
    "substitution_role",
}


class ResearchDatabaseRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> dict[str, Any]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(create=True) as conn:
            conn.executescript(SCHEMA_SQL)
            tables = self.table_names(conn)
        return {
            "status": "initialized",
            "database_path": str(self.db_path),
            "tables": tables,
            "p0_tables_present": all(table in tables for table in P0_TABLES),
            "advanced_metrics_tables_present": all(table in tables for table in ADVANCED_METRICS_TABLES),
        }

    def status(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {
                "database_path": str(self.db_path),
                "exists": False,
                "initialized": False,
                "tables": [],
                "p0_tables_present": False,
                "advanced_metrics_tables_present": False,
            }
        with self._connect(create=False) as conn:
            tables = self.table_names(conn)
        return {
            "database_path": str(self.db_path),
            "exists": True,
            "initialized": all(table in tables for table in P0_TABLES),
            "tables": tables,
            "p0_tables_present": all(table in tables for table in P0_TABLES),
            "advanced_metrics_tables_present": all(table in tables for table in ADVANCED_METRICS_TABLES),
        }

    def table_names(self, conn: sqlite3.Connection | None = None) -> list[str]:
        owns_connection = conn is None
        if conn is None:
            if not self.db_path.exists():
                return []
            conn = self._connect(create=False)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            return [str(row["name"]) for row in rows]
        finally:
            if owns_connection:
                conn.close()

    def upsert_facts(self, payload: dict[str, Any]) -> dict[str, int]:
        self.initialize()
        counts = {
            "teams": 0,
            "players": 0,
            "fixtures": 0,
            "match_results": 0,
            "squads": 0,
            "player_stats": 0,
            "player_form_snapshots": 0,
            "team_strength_snapshots": 0,
            "team_aliases": 0,
            "data_source_audit": 0,
        }
        with self._connect(create=True) as conn:
            for item in _records(payload, "teams"):
                self.upsert_team(item, conn=conn)
                counts["teams"] += 1
                counts["data_source_audit"] += 1
            for item in _records(payload, "players"):
                self.upsert_player(item, conn=conn)
                counts["players"] += 1
                counts["data_source_audit"] += 1
            for item in _records(payload, "fixtures"):
                self.upsert_fixture(item, conn=conn)
                counts["fixtures"] += 1
                counts["data_source_audit"] += 1
            for item in _records(payload, "match_results"):
                self.upsert_match_result(item, conn=conn)
                counts["match_results"] += 1
                counts["data_source_audit"] += 1
            for item in _records(payload, "squads"):
                self.upsert_squad(item, conn=conn)
                counts["squads"] += 1
                counts["data_source_audit"] += 1
            for item in _records(payload, "player_stats"):
                self.upsert_player_stat(item, conn=conn)
                counts["player_stats"] += 1
                counts["data_source_audit"] += 1
            for item in _records(payload, "player_form_snapshots"):
                self.upsert_player_form_snapshot(item, conn=conn)
                counts["player_form_snapshots"] += 1
                counts["data_source_audit"] += 1
            for item in _records(payload, "team_strength_snapshots"):
                self.upsert_team_strength_snapshot(item, conn=conn)
                counts["team_strength_snapshots"] += 1
                counts["data_source_audit"] += 1
            for item in _records(payload, "team_aliases", "aliases"):
                self.upsert_alias(item, conn=conn)
                counts["team_aliases"] += 1
                counts["data_source_audit"] += 1
        return counts

    def upsert_team(self, record: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utc_now()
        source = str(record.get("source") or "manual")
        source_team_id = _source_id(record, "source_team_id", "source_id", "team_id")
        team_id = str(
            self._source_existing_id("teams", "team_id", "source_team_id", source, source_team_id, conn=conn)
            or record.get("team_id")
            or f"team_{uuid4().hex[:12]}"
        )
        source_team_id = source_team_id or team_id
        row = {
            "team_id": team_id,
            "canonical_name": str(record["canonical_name"]),
            "country_code": record.get("country_code"),
            "fifa_code": record.get("fifa_code"),
            "source": source,
            "source_team_id": source_team_id,
            "available_at": str(record.get("available_at") or now),
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        self._execute_upsert(
            "teams",
            row,
            update_columns=(
                "canonical_name",
                "country_code",
                "fifa_code",
                "source",
                "source_team_id",
                "available_at",
                "updated_at",
            ),
            conn=conn,
        )
        self.record_audit(row["source"], "team", row["source_team_id"], "upsert", row["available_at"], {
            "team_id": row["team_id"],
            "canonical_name": row["canonical_name"],
        }, conn=conn)
        return row

    def upsert_player(self, record: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utc_now()
        source = str(record.get("source") or "manual")
        source_player_id = _source_id(record, "source_player_id", "source_id", "player_id")
        player_id = str(
            self._source_existing_id("players", "player_id", "source_player_id", source, source_player_id, conn=conn)
            or record.get("player_id")
            or f"player_{uuid4().hex[:12]}"
        )
        source_player_id = source_player_id or player_id
        row = {
            "player_id": player_id,
            "canonical_name": str(record["canonical_name"]),
            "team_id": record.get("team_id"),
            "nationality": record.get("nationality"),
            "position": record.get("position"),
            "birth_date": record.get("birth_date"),
            "club": record.get("club"),
            "source": source,
            "source_player_id": source_player_id,
            "available_at": str(record.get("available_at") or now),
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        self._execute_upsert(
            "players",
            row,
            update_columns=(
                "canonical_name",
                "team_id",
                "nationality",
                "position",
                "birth_date",
                "club",
                "source",
                "source_player_id",
                "available_at",
                "updated_at",
            ),
            conn=conn,
        )
        self.record_audit(row["source"], "player", row["source_player_id"], "upsert", row["available_at"], {
            "player_id": row["player_id"],
            "canonical_name": row["canonical_name"],
        }, conn=conn)
        return row

    def upsert_fixture(self, record: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utc_now()
        source = str(record.get("source") or "manual")
        source_fixture_id = _source_id(record, "source_fixture_id", "source_id", "fixture_id", "match_id")
        fixture_id = str(
            self._source_existing_id("fixtures", "fixture_id", "source_fixture_id", source, source_fixture_id, conn=conn)
            or record.get("fixture_id")
            or record.get("match_id")
            or f"fixture_{uuid4().hex[:12]}"
        )
        source_fixture_id = source_fixture_id or fixture_id
        row = {
            "fixture_id": fixture_id,
            "competition": record.get("competition"),
            "season": record.get("season"),
            "home_team_id": str(record["home_team_id"]),
            "away_team_id": str(record["away_team_id"]),
            "match_time": str(record["match_time"]),
            "neutral_field": 1 if bool(record.get("neutral_field", False)) else 0,
            "source": source,
            "source_fixture_id": source_fixture_id,
            "available_at": str(record.get("available_at") or now),
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        self._execute_upsert(
            "fixtures",
            row,
            update_columns=(
                "competition",
                "season",
                "home_team_id",
                "away_team_id",
                "match_time",
                "neutral_field",
                "source",
                "source_fixture_id",
                "available_at",
                "updated_at",
            ),
            conn=conn,
        )
        self.record_audit(row["source"], "fixture", row["source_fixture_id"], "upsert", row["available_at"], {
            "fixture_id": row["fixture_id"],
            "home_team_id": row["home_team_id"],
            "away_team_id": row["away_team_id"],
        }, conn=conn)
        return row

    def upsert_match_result(self, record: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utc_now()
        source = str(record.get("source") or "manual")
        source_result_id = _source_id(record, "source_result_id", "source_id", "result_id")
        result_id = str(
            self._source_existing_id(
                "match_results", "result_id", "source_result_id", source, source_result_id, conn=conn
            )
            or record.get("result_id")
            or f"result_{uuid4().hex[:12]}"
        )
        source_result_id = source_result_id or result_id
        row = {
            "result_id": result_id,
            "fixture_id": str(record["fixture_id"]),
            "home_score": int(record["home_score"]),
            "away_score": int(record["away_score"]),
            "result_status": str(record.get("result_status") or "closed"),
            "played_at": str(record.get("played_at") or record.get("match_time") or ""),
            "available_at": str(record.get("available_at") or now),
            "source": source,
            "source_result_id": source_result_id,
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        self._execute_upsert(
            "match_results",
            row,
            update_columns=(
                "fixture_id",
                "home_score",
                "away_score",
                "result_status",
                "played_at",
                "available_at",
                "source",
                "source_result_id",
                "updated_at",
            ),
            conn=conn,
        )
        self.record_audit(row["source"], "match_result", row["source_result_id"], "upsert", row["available_at"], {
            "result_id": row["result_id"],
            "fixture_id": row["fixture_id"],
            "score": [row["home_score"], row["away_score"]],
        }, conn=conn)
        return row

    def upsert_squad(self, record: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utc_now()
        source = str(record.get("source") or "manual")
        source_squad_id = _source_id(record, "source_squad_id", "source_id", "squad_id")
        squad_id = str(
            self._source_existing_id("squads", "squad_id", "source_squad_id", source, source_squad_id, conn=conn)
            or record.get("squad_id")
            or f"squad_{uuid4().hex[:12]}"
        )
        source_squad_id = source_squad_id or squad_id
        row = {
            "squad_id": squad_id,
            "team_id": str(record["team_id"]),
            "competition": record.get("competition"),
            "season": record.get("season"),
            "player_id": str(record["player_id"]),
            "role": record.get("role"),
            "shirt_number": record.get("shirt_number"),
            "available_at": str(record.get("available_at") or now),
            "source": source,
            "source_squad_id": source_squad_id,
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        self._execute_upsert(
            "squads",
            row,
            update_columns=(
                "team_id",
                "competition",
                "season",
                "player_id",
                "role",
                "shirt_number",
                "available_at",
                "source",
                "source_squad_id",
                "updated_at",
            ),
            conn=conn,
        )
        self.record_audit(row["source"], "squad", row["source_squad_id"], "upsert", row["available_at"], {
            "squad_id": row["squad_id"],
            "team_id": row["team_id"],
            "player_id": row["player_id"],
        }, conn=conn)
        return row

    def upsert_player_stat(self, record: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utc_now()
        source = str(record.get("source") or "manual")
        source_player_stat_id = _source_id(record, "source_player_stat_id", "source_id", "player_stat_id")
        player_stat_id = str(
            self._source_existing_id(
                "player_stats",
                "player_stat_id",
                "source_player_stat_id",
                source,
                source_player_stat_id,
                conn=conn,
            )
            or record.get("player_stat_id")
            or f"player_stat_{uuid4().hex[:12]}"
        )
        source_player_stat_id = source_player_stat_id or player_stat_id
        row = {
            "player_stat_id": player_stat_id,
            "player_id": str(record["player_id"]),
            "team_id": str(record["team_id"]),
            "competition": record.get("competition"),
            "season": record.get("season"),
            "stat_name": str(record["stat_name"]),
            "stat_value": float(record["stat_value"]),
            "available_at": str(record.get("available_at") or now),
            "source": source,
            "source_player_stat_id": source_player_stat_id,
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        self._execute_upsert(
            "player_stats",
            row,
            update_columns=(
                "player_id",
                "team_id",
                "competition",
                "season",
                "stat_name",
                "stat_value",
                "available_at",
                "source",
                "source_player_stat_id",
                "updated_at",
            ),
            conn=conn,
        )
        self.record_audit(row["source"], "player_stat", row["source_player_stat_id"], "upsert", row["available_at"], {
            "player_stat_id": row["player_stat_id"],
            "player_id": row["player_id"],
            "stat_name": row["stat_name"],
        }, conn=conn)
        return row

    def upsert_player_form_snapshot(
        self,
        record: dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        source = str(record.get("source") or "manual")
        source_player_id = _source_id(record, "source_player_id", "source_id", "player_id")
        as_of = str(record.get("as_of") or record.get("available_at") or now)
        snapshot_id = str(
            self._player_form_snapshot_existing_id(source, source_player_id, as_of, conn=conn)
            or record.get("snapshot_id")
            or f"player_form_{uuid4().hex[:12]}"
        )
        source_player_id = source_player_id or str(record.get("player_id") or snapshot_id)
        row = {
            "snapshot_id": snapshot_id,
            "player_id": str(record["player_id"]),
            "team_id": str(record["team_id"]),
            "club_name": record.get("club_name"),
            "club_source_id": record.get("club_source_id"),
            "as_of": as_of,
            "club_recent_matches": _int_or_none(record.get("club_recent_matches")),
            "club_recent_starts": _int_or_none(record.get("club_recent_starts")),
            "club_recent_minutes": _int_or_none(record.get("club_recent_minutes")),
            "club_recent_goals": _int_or_none(record.get("club_recent_goals")),
            "club_recent_assists": _int_or_none(record.get("club_recent_assists")),
            "national_recent_caps": _int_or_none(record.get("national_recent_caps")),
            "national_recent_starts": _int_or_none(record.get("national_recent_starts")),
            "national_recent_minutes": _int_or_none(record.get("national_recent_minutes")),
            "national_recent_goals": _int_or_none(record.get("national_recent_goals")),
            "national_recent_assists": _int_or_none(record.get("national_recent_assists")),
            "source": source,
            "source_player_id": source_player_id,
            "available_at": str(record.get("available_at") or now),
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        self._execute_upsert(
            "player_form_snapshots",
            row,
            update_columns=(
                "player_id",
                "team_id",
                "club_name",
                "club_source_id",
                "as_of",
                "club_recent_matches",
                "club_recent_starts",
                "club_recent_minutes",
                "club_recent_goals",
                "club_recent_assists",
                "national_recent_caps",
                "national_recent_starts",
                "national_recent_minutes",
                "national_recent_goals",
                "national_recent_assists",
                "source",
                "source_player_id",
                "available_at",
                "updated_at",
            ),
            conn=conn,
        )
        self.record_audit(
            row["source"],
            "player_form_snapshot",
            row["source_player_id"],
            "upsert",
            row["available_at"],
            {
                "snapshot_id": row["snapshot_id"],
                "player_id": row["player_id"],
                "team_id": row["team_id"],
                "as_of": row["as_of"],
            },
            conn=conn,
        )
        return row

    def upsert_team_strength_snapshot(
        self,
        record: dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        source = str(record.get("source") or record.get("strength_source") or "manual")
        source_team_id = _source_id(record, "source_team_id", "source_id", "team_id")
        as_of = str(record.get("as_of") or record.get("available_at") or now)
        strength_type = str(record["strength_type"])
        snapshot_id = str(
            self._team_strength_snapshot_existing_id(source, source_team_id, strength_type, as_of, conn=conn)
            or record.get("snapshot_id")
            or f"team_strength_{uuid4().hex[:12]}"
        )
        source_team_id = source_team_id or str(record.get("team_id") or snapshot_id)
        row = {
            "snapshot_id": snapshot_id,
            "team_id": str(record["team_id"]),
            "strength_type": strength_type,
            "strength_value": float(record["strength_value"]),
            "strength_source": str(record.get("strength_source") or source),
            "source": source,
            "source_team_id": source_team_id,
            "as_of": as_of,
            "available_at": str(record.get("available_at") or now),
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        self._execute_upsert(
            "team_strength_snapshots",
            row,
            update_columns=(
                "team_id",
                "strength_type",
                "strength_value",
                "strength_source",
                "source",
                "source_team_id",
                "as_of",
                "available_at",
                "updated_at",
            ),
            conn=conn,
        )
        self.record_audit(
            row["source"],
            "team_strength_snapshot",
            row["source_team_id"],
            "upsert",
            row["available_at"],
            {
                "snapshot_id": row["snapshot_id"],
                "team_id": row["team_id"],
                "strength_type": row["strength_type"],
                "as_of": row["as_of"],
            },
            conn=conn,
        )
        return row

    def upsert_alias(self, record: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utc_now()
        source = str(record.get("source") or "manual")
        source_id = str(record.get("source_id") or record.get("alias"))
        alias_id = str(
            self._alias_existing_id(
                str(record["entity_type"]),
                source,
                source_id,
                str(record["alias"]),
                conn=conn,
            )
            or record.get("alias_id")
            or f"alias_{uuid4().hex[:12]}"
        )
        row = {
            "alias_id": alias_id,
            "entity_type": str(record["entity_type"]),
            "entity_id": str(record["entity_id"]),
            "alias": str(record["alias"]),
            "source": source,
            "source_id": source_id,
            "confidence": float(record.get("confidence", 1.0)),
            "available_at": str(record.get("available_at") or now),
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        self._execute_upsert(
            "team_aliases",
            row,
            update_columns=(
                "entity_type",
                "entity_id",
                "alias",
                "source",
                "source_id",
                "confidence",
                "available_at",
                "updated_at",
            ),
            conn=conn,
        )
        self.record_audit(row["source"], "alias", row["source_id"], "upsert", row["available_at"], {
            "alias_id": row["alias_id"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "alias": row["alias"],
        }, conn=conn)
        return row

    def record_audit(
        self,
        source: str,
        source_record_type: str,
        source_record_id: str | None,
        operation: str,
        available_at: str,
        summary: dict[str, Any],
        *,
        raw_saved: bool = False,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        if conn is None:
            self.initialize()
        now = utc_now()
        row = {
            "audit_id": f"audit_{uuid4().hex[:12]}",
            "source": source,
            "source_record_type": source_record_type,
            "source_record_id": str(source_record_id or ""),
            "operation": operation,
            "available_at": available_at,
            "created_at": now,
            "summary_json": json.dumps(summary, ensure_ascii=False, sort_keys=True),
            "raw_saved": 1 if raw_saved else 0,
        }
        sql = """
            INSERT INTO data_source_audit (
                audit_id, source, source_record_type, source_record_id, operation,
                available_at, created_at, summary_json, raw_saved
            ) VALUES (
                :audit_id, :source, :source_record_type, :source_record_id, :operation,
                :available_at, :created_at, :summary_json, :raw_saved
            )
        """
        if conn is not None:
            conn.execute(sql, row)
        else:
            with self._connect(create=True) as owned_conn:
                owned_conn.execute(sql, row)
        return row

    def get_team(self, team_id: str) -> dict[str, Any] | None:
        return self._fetch_one("SELECT * FROM teams WHERE team_id = ?", (team_id,))

    def get_player(self, player_id: str) -> dict[str, Any] | None:
        return self._fetch_one("SELECT * FROM players WHERE player_id = ?", (player_id,))

    def list_player_form_snapshots(
        self,
        *,
        player_id: str | None = None,
        team_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        sql = "SELECT * FROM player_form_snapshots"
        clauses: list[str] = []
        params: list[Any] = []
        if player_id is not None:
            clauses.append("player_id = ?")
            params.append(player_id)
        if team_id is not None:
            clauses.append("team_id = ?")
            params.append(team_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY datetime(as_of) DESC, snapshot_id"
        with self._connect(create=False) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_dict(row) for row in rows]

    def list_team_strength_snapshots(self, team_id: str | None = None) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        sql = "SELECT * FROM team_strength_snapshots"
        params: tuple[Any, ...] = ()
        if team_id is not None:
            sql += " WHERE team_id = ?"
            params = (team_id,)
        sql += " ORDER BY datetime(as_of) DESC, snapshot_id"
        with self._connect(create=False) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_dict(row) for row in rows]

    def list_fixtures(
        self,
        *,
        fixture_id_prefix: str | None = None,
        source: str | None = None,
        competition_like: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        sql = "SELECT * FROM fixtures"
        clauses: list[str] = []
        params: list[Any] = []
        if fixture_id_prefix:
            clauses.append("fixture_id LIKE ?")
            params.append(f"{fixture_id_prefix}%")
        if source:
            clauses.append("source = ?")
            params.append(source)
        if competition_like:
            clauses.append("competition LIKE ?")
            params.append(competition_like)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY datetime(match_time), fixture_id"
        with self._connect(create=False) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_dict(row) for row in rows]

    def get_fixture(self, fixture_id: str) -> dict[str, Any] | None:
        return self._fetch_one("SELECT * FROM fixtures WHERE fixture_id = ?", (fixture_id,))

    def get_fixture_by_source(
        self,
        source: str,
        source_fixture_id: str,
        *,
        available_at_cutoff: str | None = None,
    ) -> dict[str, Any] | None:
        cutoff_sql, cutoff_params = _cutoff_clause("available_at", available_at_cutoff)
        return self._fetch_one(
            f"SELECT * FROM fixtures WHERE source = ? AND source_fixture_id = ?{cutoff_sql} LIMIT 1",
            (source, source_fixture_id, *cutoff_params),
        )

    def resolve_fixture(
        self,
        *,
        fixture_id: str | None = None,
        source: str | None = None,
        source_id: str | None = None,
        available_at_cutoff: str | None = None,
    ) -> dict[str, Any]:
        fixture = None
        if fixture_id:
            fixture = self.get_fixture(fixture_id)
            if fixture and not _available_at_or_before(fixture.get("available_at"), available_at_cutoff):
                fixture = None
        if fixture is None and source and source_id:
            fixture = self.get_fixture_by_source(
                source,
                source_id,
                available_at_cutoff=available_at_cutoff,
            )
        if fixture is None:
            return {
                "entity_type": "fixture",
                "fixture_id": None,
                "source": source,
                "source_id": source_id,
                "status": "unmapped",
                "confidence": 0.0,
                "reason": "no_fixture_source_mapping",
            }
        return {
            "entity_type": "fixture",
            "fixture_id": fixture["fixture_id"],
            "source": fixture.get("source"),
            "source_id": fixture.get("source_fixture_id"),
            "status": "mapped",
            "confidence": 1.0,
        }

    def row_counts(self, tables: tuple[str, ...] = P0_TABLES) -> dict[str, int]:
        if not self.db_path.exists():
            return {table: 0 for table in tables}
        counts: dict[str, int] = {}
        with self._connect(create=False) as conn:
            existing = set(self.table_names(conn))
            for table in tables:
                if table not in existing:
                    counts[table] = 0
                    continue
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return counts

    def list_audit_records(self, source_record_type: str | None = None) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        sql = "SELECT * FROM data_source_audit"
        params: tuple[Any, ...] = ()
        if source_record_type:
            sql += " WHERE source_record_type = ?"
            params = (source_record_type,)
        sql += " ORDER BY created_at, audit_id"
        with self._connect(create=False) as conn:
            return [_row_dict(row) for row in conn.execute(sql, params).fetchall()]

    def resolve_entity(
        self,
        entity_type: str,
        *,
        name: str | None = None,
        source: str | None = None,
        source_id: str | None = None,
        available_at_cutoff: str | None = None,
    ) -> dict[str, Any]:
        if not self.db_path.exists():
            return _unmapped_entity(entity_type, name=name, source=source, source_id=source_id)

        if entity_type == "team" and source and source_id:
            cutoff_sql, cutoff_params = _cutoff_clause("available_at", available_at_cutoff)
            team = self._fetch_one(
                f"SELECT * FROM teams WHERE source = ? AND source_team_id = ?{cutoff_sql}",
                (source, source_id, *cutoff_params),
            )
            if team:
                return _mapped_entity(entity_type, team["team_id"], name, source, source_id, 1.0)
        if entity_type == "player" and source and source_id:
            cutoff_sql, cutoff_params = _cutoff_clause("available_at", available_at_cutoff)
            player = self._fetch_one(
                f"SELECT * FROM players WHERE source = ? AND source_player_id = ?{cutoff_sql}",
                (source, source_id, *cutoff_params),
            )
            if player:
                return _mapped_entity(entity_type, player["player_id"], name, source, source_id, 1.0)

        if source and source_id:
            cutoff_sql, cutoff_params = _cutoff_clause("available_at", available_at_cutoff)
            alias = self._fetch_one(
                f"""
                SELECT * FROM team_aliases
                WHERE entity_type = ? AND source = ? AND source_id = ?
                {cutoff_sql}
                ORDER BY confidence DESC
                LIMIT 1
                """,
                (entity_type, source, source_id, *cutoff_params),
            )
            if alias:
                if not self._alias_target_available(entity_type, alias["entity_id"], available_at_cutoff):
                    return _unmapped_entity(
                        entity_type,
                        name=name,
                        source=source,
                        source_id=source_id,
                        reason="target_entity_unavailable_at_cutoff",
                    )
                return _mapped_entity(entity_type, alias["entity_id"], name, source, source_id, alias["confidence"])

        if name:
            if entity_type == "team":
                cutoff_sql, cutoff_params = _cutoff_clause("available_at", available_at_cutoff)
                team = self._fetch_one(
                    f"SELECT * FROM teams WHERE lower(canonical_name) = lower(?){cutoff_sql} LIMIT 1",
                    (name, *cutoff_params),
                )
                if team:
                    return _mapped_entity(entity_type, team["team_id"], name, source, source_id, 1.0)
            elif entity_type == "player":
                cutoff_sql, cutoff_params = _cutoff_clause("available_at", available_at_cutoff)
                player = self._fetch_one(
                    f"SELECT * FROM players WHERE lower(canonical_name) = lower(?){cutoff_sql} LIMIT 1",
                    (name, *cutoff_params),
                )
                if player:
                    return _mapped_entity(entity_type, player["player_id"], name, source, source_id, 1.0)

            cutoff_sql, cutoff_params = _cutoff_clause("available_at", available_at_cutoff)
            alias = self._fetch_one(
                f"""
                SELECT * FROM team_aliases
                WHERE entity_type = ? AND lower(alias) = lower(?)
                {cutoff_sql}
                ORDER BY confidence DESC
                LIMIT 1
                """,
                (entity_type, name, *cutoff_params),
            )
            if alias:
                if not self._alias_target_available(entity_type, alias["entity_id"], available_at_cutoff):
                    return _unmapped_entity(
                        entity_type,
                        name=name,
                        source=source,
                        source_id=source_id,
                        reason="target_entity_unavailable_at_cutoff",
                    )
                return _mapped_entity(entity_type, alias["entity_id"], name, source, source_id, alias["confidence"])

        return _unmapped_entity(entity_type, name=name, source=source, source_id=source_id)

    def recent_results_for_team(
        self,
        team_id: str,
        *,
        match_time: str,
        available_at_cutoff: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with self._connect(create=False) as conn:
            rows = conn.execute(
                """
                SELECT
                    mr.*,
                    f.home_team_id,
                    f.away_team_id,
                    f.match_time,
                    f.competition,
                    f.season
                FROM match_results mr
                JOIN fixtures f ON f.fixture_id = mr.fixture_id
                WHERE (f.home_team_id = ? OR f.away_team_id = ?)
                  AND datetime(mr.available_at) <= datetime(?)
                  AND datetime(f.available_at) <= datetime(?)
                  AND datetime(COALESCE(NULLIF(mr.played_at, ''), f.match_time)) < datetime(?)
                ORDER BY datetime(COALESCE(NULLIF(mr.played_at, ''), f.match_time)) DESC
                LIMIT ?
                """,
                (team_id, team_id, available_at_cutoff, available_at_cutoff, match_time, limit),
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def blocked_results_after_cutoff(
        self,
        team_id: str,
        *,
        match_time: str,
        available_at_cutoff: str,
    ) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with self._connect(create=False) as conn:
            rows = conn.execute(
                """
                SELECT
                    mr.result_id,
                    mr.fixture_id,
                    mr.available_at,
                    mr.source,
                    mr.source_result_id,
                    f.available_at AS fixture_available_at,
                    f.match_time
                FROM match_results mr
                JOIN fixtures f ON f.fixture_id = mr.fixture_id
                WHERE (f.home_team_id = ? OR f.away_team_id = ?)
                  AND (
                    datetime(mr.available_at) > datetime(?)
                    OR datetime(f.available_at) > datetime(?)
                  )
                  AND datetime(COALESCE(NULLIF(mr.played_at, ''), f.match_time)) < datetime(?)
                ORDER BY datetime(mr.available_at)
                """,
                (team_id, team_id, available_at_cutoff, available_at_cutoff, match_time),
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def save_feature_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        now = utc_now()
        row = {
            "snapshot_id": snapshot["snapshot_id"],
            "match_id": snapshot["match_id"],
            "home_team_id": snapshot.get("home_team_id"),
            "away_team_id": snapshot.get("away_team_id"),
            "generated_at": snapshot["generated_at"],
            "as_of": snapshot["as_of"],
            "available_at_cutoff": snapshot["available_at_cutoff"],
            "feature_json": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            "coverage_json": json.dumps(snapshot.get("coverage_report", {}), ensure_ascii=False, sort_keys=True),
            "source_audit_json": json.dumps(snapshot.get("source_audit", []), ensure_ascii=False, sort_keys=True),
            "not_used_in_scoring_by_default": 1 if snapshot.get("not_used_in_scoring_by_default", True) else 0,
            "created_at": now,
        }
        with self._connect(create=True) as conn:
            conn.execute(
                """
                INSERT INTO feature_snapshots (
                    snapshot_id, match_id, home_team_id, away_team_id, generated_at, as_of,
                    available_at_cutoff, feature_json, coverage_json, source_audit_json,
                    not_used_in_scoring_by_default, created_at
                ) VALUES (
                    :snapshot_id, :match_id, :home_team_id, :away_team_id, :generated_at, :as_of,
                    :available_at_cutoff, :feature_json, :coverage_json, :source_audit_json,
                    :not_used_in_scoring_by_default, :created_at
                )
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    match_id = excluded.match_id,
                    home_team_id = excluded.home_team_id,
                    away_team_id = excluded.away_team_id,
                    generated_at = excluded.generated_at,
                    as_of = excluded.as_of,
                    available_at_cutoff = excluded.available_at_cutoff,
                    feature_json = excluded.feature_json,
                    coverage_json = excluded.coverage_json,
                    source_audit_json = excluded.source_audit_json,
                    not_used_in_scoring_by_default = excluded.not_used_in_scoring_by_default
                """,
                row,
            )
        self.record_audit("research_db", "feature_snapshot", snapshot["snapshot_id"], "upsert", snapshot["as_of"], {
            "match_id": snapshot["match_id"],
            "available_at_cutoff": snapshot["available_at_cutoff"],
        })
        return snapshot

    def list_feature_snapshots(self, match_id: str | None = None) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        sql = "SELECT * FROM feature_snapshots"
        params: tuple[Any, ...] = ()
        if match_id is not None:
            sql += " WHERE match_id = ?"
            params = (match_id,)
        sql += " ORDER BY generated_at, snapshot_id"
        with self._connect(create=False) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._snapshot_from_row(row) for row in rows]

    def get_feature_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        if not self.db_path.exists():
            return None
        row = self._fetch_one("SELECT * FROM feature_snapshots WHERE snapshot_id = ?", (snapshot_id,))
        return self._snapshot_from_row(row) if row else None

    def latest_feature_snapshot(self, match_id: str) -> dict[str, Any] | None:
        snapshots = self.list_feature_snapshots(match_id=match_id)
        return snapshots[-1] if snapshots else None

    def save_post_match_advanced_metrics(
        self,
        events: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        self.initialize()
        now = utc_now()
        match_id = str(summary["match_id"])
        summary_id = str(summary.get("summary_id") or f"pmam_{uuid4().hex[:12]}")
        safe_summary = _safe_advanced_summary(summary, summary_id=summary_id)
        with self._connect(create=True) as conn:
            for event in events:
                row = {
                    "event_id": str(event["event_id"]),
                    "match_id": str(event["match_id"]),
                    "minute": event.get("minute"),
                    "team_id": event.get("team_id"),
                    "player_id": event.get("player_id"),
                    "event_type": str(event["event_type"]),
                    "qualifier_json": json.dumps(
                        _safe_advanced_qualifier(event.get("qualifier", {})),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "source": str(event.get("source") or "post_match_advanced_metrics_proxy"),
                    "confidence": float(event.get("confidence", 0.8)),
                    "raw_saved": 0,
                    "created_at": now,
                }
                conn.execute(
                    """
                    INSERT INTO post_match_advanced_events (
                        event_id, match_id, minute, team_id, player_id, event_type,
                        qualifier_json, source, confidence, raw_saved, created_at
                    ) VALUES (
                        :event_id, :match_id, :minute, :team_id, :player_id, :event_type,
                        :qualifier_json, :source, :confidence, :raw_saved, :created_at
                    )
                    ON CONFLICT(match_id, event_id) DO UPDATE SET
                        minute = excluded.minute,
                        team_id = excluded.team_id,
                        player_id = excluded.player_id,
                        event_type = excluded.event_type,
                        qualifier_json = excluded.qualifier_json,
                        source = excluded.source,
                        confidence = excluded.confidence,
                        raw_saved = excluded.raw_saved
                """,
                    row,
                )

            summary_row = {
                "summary_id": summary_id,
                "match_id": match_id,
                "generated_at": now,
                "metrics_json": json.dumps(safe_summary, ensure_ascii=False, sort_keys=True),
                "source_audit_json": json.dumps(safe_summary.get("source_policy", {}), ensure_ascii=False, sort_keys=True),
                "proxy": 1,
                "not_official_xg": 1,
                "raw_saved": 0,
                "created_at": now,
            }
            conn.execute(
                """
                INSERT INTO post_match_advanced_metric_summaries (
                    summary_id, match_id, generated_at, metrics_json, source_audit_json,
                    proxy, not_official_xg, raw_saved, created_at
                ) VALUES (
                    :summary_id, :match_id, :generated_at, :metrics_json, :source_audit_json,
                    :proxy, :not_official_xg, :raw_saved, :created_at
                )
                ON CONFLICT(summary_id) DO UPDATE SET
                    match_id = excluded.match_id,
                    generated_at = excluded.generated_at,
                    metrics_json = excluded.metrics_json,
                    source_audit_json = excluded.source_audit_json,
                    proxy = excluded.proxy,
                    not_official_xg = excluded.not_official_xg,
                    raw_saved = excluded.raw_saved
                """,
                summary_row,
            )
            self.record_audit(
                "post_match_advanced_metrics_proxy",
                "advanced_metrics_summary",
                summary_id,
                "upsert",
                now,
                {
                    "match_id": match_id,
                    "events_count": len(events),
                    "proxy": True,
                    "not_official_xg": True,
                },
                raw_saved=False,
                conn=conn,
            )
        return {
            "status": "ok",
            "summary_id": summary_id,
            "events_saved": len(events),
            "summary_saved": True,
            "raw_saved": False,
        }

    def list_post_match_advanced_events(self, match_id: str) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with self._connect(create=False) as conn:
            rows = conn.execute(
                """
                SELECT * FROM post_match_advanced_events
                WHERE match_id = ?
                ORDER BY minute IS NULL, minute, event_id
                """,
                (match_id,),
            ).fetchall()
        return [self._advanced_event_from_row(row) for row in rows]

    def list_post_match_advanced_metric_summaries(self, match_id: str) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with self._connect(create=False) as conn:
            rows = conn.execute(
                """
                SELECT * FROM post_match_advanced_metric_summaries
                WHERE match_id = ?
                ORDER BY generated_at, summary_id
                """,
                (match_id,),
            ).fetchall()
        return [self._advanced_summary_from_row(row) for row in rows]

    def latest_post_match_advanced_metrics(self, match_id: str) -> dict[str, Any] | None:
        summaries = self.list_post_match_advanced_metric_summaries(match_id)
        return summaries[-1] if summaries else None

    def _snapshot_from_row(self, row: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
        data = _row_dict(row)
        snapshot = json.loads(data["feature_json"])
        snapshot.setdefault("snapshot_id", data["snapshot_id"])
        snapshot.setdefault("match_id", data["match_id"])
        snapshot.setdefault("coverage_report", json.loads(data.get("coverage_json") or "{}"))
        snapshot.setdefault("source_audit", json.loads(data.get("source_audit_json") or "[]"))
        snapshot["not_used_in_scoring_by_default"] = bool(data["not_used_in_scoring_by_default"])
        return snapshot

    def _advanced_event_from_row(self, row: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
        data = _row_dict(row)
        data["qualifier"] = json.loads(data.pop("qualifier_json") or "{}")
        data["raw_saved"] = bool(data["raw_saved"])
        return data

    def _advanced_summary_from_row(self, row: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
        data = _row_dict(row)
        summary = json.loads(data["metrics_json"])
        summary.setdefault("summary_id", data["summary_id"])
        summary.setdefault("match_id", data["match_id"])
        summary["proxy"] = bool(data["proxy"])
        summary["not_official_xg"] = bool(data["not_official_xg"])
        summary["raw_saved"] = bool(data["raw_saved"])
        summary["generated_at"] = data["generated_at"]
        return summary

    def _execute_upsert(
        self,
        table: str,
        row: dict[str, Any],
        *,
        update_columns: tuple[str, ...],
        conn: sqlite3.Connection | None = None,
    ) -> None:
        if conn is None:
            self.initialize()
        columns = tuple(row)
        placeholders = ", ".join(f":{column}" for column in columns)
        update_set = ", ".join(f"{column} = excluded.{column}" for column in update_columns)
        sql = f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT({columns[0]}) DO UPDATE SET {update_set}
        """
        if conn is not None:
            conn.execute(sql, row)
        else:
            with self._connect(create=True) as owned_conn:
                owned_conn.execute(sql, row)

    def _fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        if not self.db_path.exists():
            return None
        with self._connect(create=False) as conn:
            row = conn.execute(sql, params).fetchone()
        return _row_dict(row) if row else None

    def _source_existing_id(
        self,
        table: str,
        id_column: str,
        source_id_column: str,
        source: str,
        source_record_id: str | None,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> str | None:
        if not source_record_id:
            return None
        sql = f"SELECT {id_column} FROM {table} WHERE source = ? AND {source_id_column} = ? LIMIT 1"
        params = (source, source_record_id)
        if conn is not None:
            row = conn.execute(sql, params).fetchone()
            return str(row[id_column]) if row else None
        row = self._fetch_one(sql, params)
        return str(row[id_column]) if row else None

    def _player_form_snapshot_existing_id(
        self,
        source: str,
        source_player_id: str | None,
        as_of: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> str | None:
        if not source_player_id:
            return None
        sql = """
            SELECT snapshot_id FROM player_form_snapshots
            WHERE source = ? AND source_player_id = ? AND as_of = ?
            LIMIT 1
        """
        params = (source, source_player_id, as_of)
        if conn is not None:
            row = conn.execute(sql, params).fetchone()
            return str(row["snapshot_id"]) if row else None
        row = self._fetch_one(sql, params)
        return str(row["snapshot_id"]) if row else None

    def _team_strength_snapshot_existing_id(
        self,
        source: str,
        source_team_id: str | None,
        strength_type: str,
        as_of: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> str | None:
        if not source_team_id:
            return None
        sql = """
            SELECT snapshot_id FROM team_strength_snapshots
            WHERE source = ? AND source_team_id = ? AND strength_type = ? AND as_of = ?
            LIMIT 1
        """
        params = (source, source_team_id, strength_type, as_of)
        if conn is not None:
            row = conn.execute(sql, params).fetchone()
            return str(row["snapshot_id"]) if row else None
        row = self._fetch_one(sql, params)
        return str(row["snapshot_id"]) if row else None

    def _alias_existing_id(
        self,
        entity_type: str,
        source: str,
        source_id: str,
        alias: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> str | None:
        sql = """
            SELECT alias_id FROM team_aliases
            WHERE entity_type = ? AND source = ? AND source_id = ? AND lower(alias) = lower(?)
            LIMIT 1
        """
        params = (entity_type, source, source_id, alias)
        if conn is not None:
            row = conn.execute(sql, params).fetchone()
            return str(row["alias_id"]) if row else None
        row = self._fetch_one(sql, params)
        return str(row["alias_id"]) if row else None

    def _alias_target_available(
        self,
        entity_type: str,
        entity_id: str,
        available_at_cutoff: str | None,
    ) -> bool:
        if entity_type == "competition" or not available_at_cutoff:
            return True
        table = "teams" if entity_type == "team" else "players" if entity_type == "player" else ""
        id_column = "team_id" if entity_type == "team" else "player_id" if entity_type == "player" else ""
        if not table:
            return False
        row = self._fetch_one(
            f"SELECT {id_column} FROM {table} WHERE {id_column} = ? AND datetime(available_at) <= datetime(?)",
            (entity_id, available_at_cutoff),
        )
        return row is not None

    def _connect(self, *, create: bool) -> sqlite3.Connection:
        if not create and not self.db_path.exists():
            raise FileNotFoundError(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _records(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
    return records


def _safe_advanced_qualifier(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _drop_raw_like_keys(item)
        for key, item in value.items()
        if str(key) in SAFE_ADVANCED_QUALIFIER_KEYS and not _raw_like_key(str(key))
    }


def _safe_advanced_summary(summary: dict[str, Any], *, summary_id: str) -> dict[str, Any]:
    safe = _drop_raw_like_keys(summary)
    if not isinstance(safe, dict):
        safe = {}
    safe["summary_id"] = summary_id
    safe["proxy"] = True
    safe["not_official_xg"] = True
    safe["raw_saved"] = False
    source_policy = safe.get("source_policy") if isinstance(safe.get("source_policy"), dict) else {}
    safe["source_policy"] = {
        **source_policy,
        "proxy": True,
        "not_official_xg": True,
        "raw_saved": False,
        "raw_payload_saved": False,
        "not_used_in_scoring": True,
    }
    return safe


def _drop_raw_like_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_raw_like_keys(item)
            for key, item in value.items()
            if not _raw_like_key(str(key))
        }
    if isinstance(value, list):
        return [_drop_raw_like_keys(item) for item in value]
    return value


def _raw_like_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in {"raw", "raw_text", "text", "commentary", "provider_payload", "payload", "raw_payload"}


def _source_id(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if record.get(key):
            return str(record[key])
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _cutoff_clause(column: str, available_at_cutoff: str | None) -> tuple[str, tuple[str, ...]]:
    if not available_at_cutoff:
        return "", ()
    return f" AND datetime({column}) <= datetime(?)", (available_at_cutoff,)


def _available_at_or_before(available_at: Any, cutoff: str | None) -> bool:
    if not cutoff:
        return True
    available_dt = _parse_datetime(str(available_at or ""))
    cutoff_dt = _parse_datetime(cutoff)
    if available_dt is None or cutoff_dt is None:
        return False
    return available_dt <= cutoff_dt


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_dict(row: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def _mapped_entity(
    entity_type: str,
    entity_id: str,
    name: str | None,
    source: str | None,
    source_id: str | None,
    confidence: float,
) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "input": name,
        "source": source,
        "source_id": source_id,
        "entity_id": entity_id,
        "status": "mapped",
        "confidence": float(confidence),
    }


def _unmapped_entity(
    entity_type: str,
    *,
    name: str | None,
    source: str | None,
    source_id: str | None,
    reason: str = "no_alias_or_source_mapping",
) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "input": name,
        "source": source,
        "source_id": source_id,
        "entity_id": None,
        "status": "unmapped",
        "confidence": 0.0,
        "reason": reason,
    }


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    country_code TEXT,
    fifa_code TEXT,
    source TEXT NOT NULL,
    source_team_id TEXT NOT NULL,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_team_id)
);

CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    team_id TEXT,
    nationality TEXT,
    position TEXT,
    birth_date TEXT,
    club TEXT,
    source TEXT NOT NULL,
    source_player_id TEXT NOT NULL,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_player_id)
);

CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id TEXT PRIMARY KEY,
    competition TEXT,
    season TEXT,
    home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    match_time TEXT NOT NULL,
    neutral_field INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    source_fixture_id TEXT NOT NULL,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_fixture_id)
);

CREATE TABLE IF NOT EXISTS match_results (
    result_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    result_status TEXT NOT NULL,
    played_at TEXT,
    available_at TEXT NOT NULL,
    source TEXT NOT NULL,
    source_result_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_result_id)
);

CREATE TABLE IF NOT EXISTS squads (
    squad_id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    competition TEXT,
    season TEXT,
    player_id TEXT NOT NULL,
    role TEXT,
    shirt_number INTEGER,
    available_at TEXT NOT NULL,
    source TEXT NOT NULL,
    source_squad_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_squad_id)
);

CREATE TABLE IF NOT EXISTS player_stats (
    player_stat_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    competition TEXT,
    season TEXT,
    stat_name TEXT NOT NULL,
    stat_value REAL NOT NULL,
    available_at TEXT NOT NULL,
    source TEXT NOT NULL,
    source_player_stat_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_player_stat_id)
);

CREATE TABLE IF NOT EXISTS player_form_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    club_name TEXT,
    club_source_id TEXT,
    as_of TEXT NOT NULL,
    club_recent_matches INTEGER,
    club_recent_starts INTEGER,
    club_recent_minutes INTEGER,
    club_recent_goals INTEGER,
    club_recent_assists INTEGER,
    national_recent_caps INTEGER,
    national_recent_starts INTEGER,
    national_recent_minutes INTEGER,
    national_recent_goals INTEGER,
    national_recent_assists INTEGER,
    source TEXT NOT NULL,
    source_player_id TEXT NOT NULL,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_player_id, as_of)
);

CREATE TABLE IF NOT EXISTS team_strength_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    strength_type TEXT NOT NULL,
    strength_value REAL NOT NULL,
    strength_source TEXT NOT NULL,
    source TEXT NOT NULL,
    source_team_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_team_id, strength_type, as_of)
);

CREATE TABLE IF NOT EXISTS team_aliases (
    alias_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('team', 'player', 'competition')),
    entity_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    confidence REAL NOT NULL,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(entity_type, source, source_id, alias)
);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL,
    home_team_id TEXT,
    away_team_id TEXT,
    generated_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    available_at_cutoff TEXT NOT NULL,
    feature_json TEXT NOT NULL,
    coverage_json TEXT NOT NULL,
    source_audit_json TEXT NOT NULL,
    not_used_in_scoring_by_default INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_match_advanced_events (
    event_id TEXT NOT NULL,
    match_id TEXT NOT NULL,
    minute INTEGER,
    team_id TEXT,
    player_id TEXT,
    event_type TEXT NOT NULL,
    qualifier_json TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL,
    raw_saved INTEGER NOT NULL DEFAULT 0 CHECK(raw_saved = 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY(match_id, event_id)
);

CREATE TABLE IF NOT EXISTS post_match_advanced_metric_summaries (
    summary_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    source_audit_json TEXT NOT NULL,
    proxy INTEGER NOT NULL CHECK(proxy = 1),
    not_official_xg INTEGER NOT NULL CHECK(not_official_xg = 1),
    raw_saved INTEGER NOT NULL DEFAULT 0 CHECK(raw_saved = 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_source_audit (
    audit_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_record_type TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    raw_saved INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_team_aliases_lookup
ON team_aliases(entity_type, source, source_id, alias);

CREATE INDEX IF NOT EXISTS idx_match_results_fixture_available
ON match_results(fixture_id, available_at);

CREATE INDEX IF NOT EXISTS idx_player_form_snapshots_player_as_of
ON player_form_snapshots(player_id, as_of);

CREATE INDEX IF NOT EXISTS idx_team_strength_snapshots_team_as_of
ON team_strength_snapshots(team_id, as_of);

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_match
ON feature_snapshots(match_id, generated_at);

CREATE INDEX IF NOT EXISTS idx_post_match_advanced_events_match
ON post_match_advanced_events(match_id, minute);

CREATE INDEX IF NOT EXISTS idx_post_match_advanced_metric_summaries_match
ON post_match_advanced_metric_summaries(match_id, generated_at);
"""
