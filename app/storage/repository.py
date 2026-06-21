from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .json_store import JsonStore


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


FORMAL_REMEDIATION_ACTIVE_STATUSES = {
    "queued",
    "waiting_window",
    "running",
    "retry_scheduled",
}
FORMAL_REMEDIATION_RUNNABLE_STATUSES = {
    "queued",
    "waiting_window",
    "retry_scheduled",
}
FORMAL_REMEDIATION_FINAL_STATUSES = {
    "succeeded",
    "terminal_blocked",
    "max_attempts_exhausted",
    "send_failed",
}


def is_formal_remediation_job_runnable(job: dict[str, Any] | None, *, as_of: str | None = None) -> bool:
    if not isinstance(job, dict):
        return False
    if str(job.get("status") or "") not in FORMAL_REMEDIATION_RUNNABLE_STATUSES:
        return False
    cutoff = str(as_of or utc_now())
    return str(job.get("next_run_after") or cutoff) <= cutoff


class LocalRepository:
    def __init__(self, store: JsonStore) -> None:
        self.store = store

    def create_subscription(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        now = utc_now()
        subscription = {
            "subscription_id": f"sub_{uuid4().hex[:12]}",
            "status": "active",
            "created_at": now,
            "updated_at": now,
            **payload,
        }
        state["subscriptions"].append(subscription)
        self.store.save(state)
        return subscription

    def upsert_matches(self, matches: list[dict[str, Any]]) -> int:
        if not matches:
            return 0
        state = self.store.load()
        existing = {item["match_id"]: item for item in state["matches"]}
        now = utc_now()
        for match in matches:
            match_id = match["match_id"]
            previous = existing.get(match_id, {})
            existing[match_id] = {
                **previous,
                **match,
                "created_at": previous.get("created_at", now),
                "updated_at": now,
            }
        state["matches"] = sorted(existing.values(), key=lambda item: item["match_id"])
        self.store.save(state)
        return len(matches)

    def list_matches(self) -> list[dict[str, Any]]:
        return self.store.load()["matches"]

    def get_match(self, match_id: str) -> dict[str, Any] | None:
        for item in self.store.load()["matches"]:
            if item["match_id"] == match_id:
                return item
        return None

    def append_odds_snapshots(self, snapshots: list[dict[str, Any]]) -> int:
        if not snapshots:
            return 0
        state = self.store.load()
        state["odds_snapshots"].extend(snapshots)
        self.store.save(state)
        return len(snapshots)

    def list_odds_snapshots(self, match_id: str) -> list[dict[str, Any]]:
        return [item for item in self.store.load()["odds_snapshots"] if item["match_id"] == match_id]

    def latest_odds_snapshots(self, match_id: str) -> list[dict[str, Any]]:
        snapshots = self.list_odds_snapshots(match_id)
        if not snapshots:
            return []
        latest = max(item["captured_at"] for item in snapshots if item.get("captured_at"))
        return [item for item in snapshots if item.get("captured_at") == latest]

    def list_subscriptions(self, match_id: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
        subscriptions = self.store.load()["subscriptions"]
        if match_id is not None:
            subscriptions = [item for item in subscriptions if item["match_id"] == match_id]
        if active_only:
            subscriptions = [item for item in subscriptions if item["status"] == "active"]
        return subscriptions

    def delete_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        state = self.store.load()
        now = utc_now()
        deleted: dict[str, Any] | None = None
        for item in state["subscriptions"]:
            if item["subscription_id"] == subscription_id:
                item["status"] = "deleted"
                item["updated_at"] = now
                deleted = item
                break
        if deleted is not None:
            self.store.save(state)
        return deleted

    def delete_active_subscription(
        self,
        match_id: str,
        platform: str,
        platform_user_id: str,
        chat_id: str,
    ) -> dict[str, Any] | None:
        state = self.store.load()
        now = utc_now()
        deleted: dict[str, Any] | None = None
        for item in state["subscriptions"]:
            if (
                item["match_id"] == match_id
                and item["platform"] == platform
                and item["platform_user_id"] == platform_user_id
                and item["chat_id"] == chat_id
                and item["status"] == "active"
            ):
                item["status"] = "deleted"
                item["updated_at"] = now
                deleted = item
                break
        if deleted is not None:
            self.store.save(state)
        return deleted

    def save_report(self, report: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        state["agent_reports"].append(report)
        self.store.save(state)
        return report

    def latest_report(self, match_id: str) -> dict[str, Any] | None:
        reports = [item for item in self.store.load()["agent_reports"] if item["match_id"] == match_id]
        if not reports:
            return None
        return sorted(reports, key=lambda item: item["created_at"])[-1]

    def add_push_logs(self, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not logs:
            return []
        state = self.store.load()
        state["push_logs"].extend(logs)
        self.store.save(state)
        return logs

    def list_push_logs(self, match_id: str | None = None) -> list[dict[str, Any]]:
        logs = self.store.load()["push_logs"]
        if match_id is not None:
            logs = [item for item in logs if item["match_id"] == match_id]
        return logs

    def enqueue_formal_remediation_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            now = utc_now()
            fixture_id = str(payload.get("fixture_id") or "")
            repair_signature = str(payload.get("repair_signature") or "")
            continue_attempt_token = str(payload.get("continue_attempt_token") or "").strip()
            existing = next(
                (
                    item
                    for item in state["formal_remediation_jobs"]
                    if item.get("fixture_id") == fixture_id
                    and item.get("repair_signature") == repair_signature
                    and str(item.get("status") or "") in FORMAL_REMEDIATION_ACTIVE_STATUSES
                ),
                None,
            )
            if existing is not None:
                existing["updated_at"] = now
                existing["blocked_reason_codes"] = _merge_sorted_strings(
                    existing.get("blocked_reason_codes"),
                    payload.get("blocked_reason_codes"),
                )
                existing["repair_actions"] = _merge_repair_actions(
                    existing.get("repair_actions"),
                    payload.get("repair_actions"),
                )
                existing["pending_audiences"] = _merge_sorted_strings(
                    existing.get("pending_audiences"),
                    payload.get("pending_audiences"),
                )
                existing["push_types"] = _merge_sorted_strings(
                    existing.get("push_types"),
                    payload.get("push_types"),
                )
                if payload.get("audit_context"):
                    existing["audit_context"] = {
                        **dict(existing.get("audit_context") or {}),
                        **dict(payload.get("audit_context") or {}),
                    }
                if payload.get("watch_context"):
                    existing["watch_context"] = {
                        **dict(existing.get("watch_context") or {}),
                        **dict(payload.get("watch_context") or {}),
                    }
                if payload.get("research_db_path"):
                    existing["research_db_path"] = payload.get("research_db_path")
                if payload.get("match_time"):
                    existing["match_time"] = payload.get("match_time")
                existing["repairable"] = bool(payload.get("repairable", existing.get("repairable", False)))
                existing["max_attempts"] = int(payload.get("max_attempts") or existing.get("max_attempts") or 0)
                tokens = list(existing.get("continue_attempt_tokens") or [])
                if continue_attempt_token and continue_attempt_token not in tokens:
                    tokens.append(continue_attempt_token)
                    existing["continue_attempt_count"] = int(existing.get("continue_attempt_count") or 0) + 1
                existing["continue_attempt_tokens"] = tokens
                next_run_after = payload.get("next_run_after")
                if next_run_after:
                    current = str(existing.get("next_run_after") or "")
                    if not current or str(next_run_after) < current:
                        existing["next_run_after"] = next_run_after
                if str(existing.get("status") or "") == "waiting_window":
                    due = str(existing.get("next_run_after") or "")
                    if due and due <= now:
                        existing["status"] = "queued"
                return deepcopy(existing)

            status = str(payload.get("status") or "")
            next_run_after = payload.get("next_run_after")
            if not status:
                status = "waiting_window" if next_run_after and str(next_run_after) > now else "queued"
            job = {
                "job_id": str(payload.get("job_id") or f"remed_{uuid4().hex[:12]}"),
                "fixture_id": fixture_id,
                "repair_signature": repair_signature,
                "status": status,
                "repairable": bool(payload.get("repairable", True)),
                "blocked_reason_codes": _merge_sorted_strings([], payload.get("blocked_reason_codes")),
                "repair_actions": _merge_repair_actions([], payload.get("repair_actions")),
                "pending_audiences": _merge_sorted_strings([], payload.get("pending_audiences")),
                "push_types": _merge_sorted_strings([], payload.get("push_types")),
                "attempt_count": int(payload.get("attempt_count") or 0),
                "continue_attempt_count": 1 if continue_attempt_token else 0,
                "continue_attempt_tokens": [continue_attempt_token] if continue_attempt_token else [],
                "max_attempts": int(payload.get("max_attempts") or 0),
                "next_run_after": next_run_after,
                "last_error": payload.get("last_error"),
                "last_attempt_id": payload.get("last_attempt_id"),
                "last_send_results": list(payload.get("last_send_results") or []),
                "audit_context": dict(payload.get("audit_context") or {}),
                "watch_context": dict(payload.get("watch_context") or {}),
                "research_db_path": payload.get("research_db_path"),
                "used_research_db_fallback": bool(payload.get("used_research_db_fallback", False)),
                "match_time": payload.get("match_time"),
                "created_at": now,
                "updated_at": now,
            }
            state["formal_remediation_jobs"].append(job)
            state["formal_remediation_jobs"] = sorted(
                state["formal_remediation_jobs"],
                key=lambda item: (
                    str(item.get("next_run_after") or ""),
                    str(item.get("created_at") or ""),
                    str(item.get("job_id") or ""),
                ),
            )
            return deepcopy(job)

        return self.store.update(mutate)

    def list_formal_remediation_jobs(
        self,
        *,
        fixture_id: str | None = None,
        status: str | None = None,
        due_only: bool = False,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        jobs = self.store.load()["formal_remediation_jobs"]
        if fixture_id is not None:
            jobs = [item for item in jobs if item.get("fixture_id") == fixture_id]
        if status is not None:
            jobs = [item for item in jobs if item.get("status") == status]
        if due_only:
            cutoff = str(as_of or utc_now())
            jobs = [item for item in jobs if is_formal_remediation_job_runnable(item, as_of=cutoff)]
        return sorted(
            jobs,
            key=lambda item: (
                str(item.get("next_run_after") or ""),
                str(item.get("created_at") or ""),
                str(item.get("job_id") or ""),
            ),
        )

    def get_formal_remediation_job(self, job_id: str) -> dict[str, Any] | None:
        for job in self.store.load()["formal_remediation_jobs"]:
            if job.get("job_id") == job_id:
                return deepcopy(job)
        return None

    def is_formal_remediation_job_runnable(self, job: dict[str, Any] | None, *, as_of: str | None = None) -> bool:
        return is_formal_remediation_job_runnable(job, as_of=as_of)

    def start_formal_remediation_job(self, job_id: str, *, as_of: str | None = None) -> dict[str, Any] | None:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
            now = str(as_of or utc_now())
            for job in state["formal_remediation_jobs"]:
                if job.get("job_id") != job_id:
                    continue
                if not is_formal_remediation_job_runnable(job, as_of=now):
                    return None
                job["status"] = "running"
                job["attempt_count"] = int(job.get("attempt_count") or 0) + 1
                job["updated_at"] = now
                job["last_started_at"] = now
                return deepcopy(job)
            return None

        return self.store.update(mutate)

    def finish_formal_remediation_job(
        self,
        job_id: str,
        *,
        status: str,
        next_run_after: str | None = None,
        last_error: str | None = None,
        last_attempt_id: str | None = None,
        last_send_results: list[dict[str, Any]] | None = None,
        pending_audiences: list[str] | None = None,
    ) -> dict[str, Any] | None:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
            now = utc_now()
            for job in state["formal_remediation_jobs"]:
                if job.get("job_id") != job_id:
                    continue
                job["status"] = status
                job["updated_at"] = now
                job["finished_at"] = now
                job["next_run_after"] = next_run_after
                job["last_error"] = last_error
                if last_attempt_id is not None:
                    job["last_attempt_id"] = last_attempt_id
                if last_send_results is not None:
                    job["last_send_results"] = list(last_send_results)
                if pending_audiences is not None:
                    job["pending_audiences"] = _merge_sorted_strings(pending_audiences)
                return deepcopy(job)
            return None

        return self.store.update(mutate)

    def add_formal_remediation_attempt(self, attempt: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            state["formal_remediation_attempts"].append(attempt)
            state["formal_remediation_attempts"] = sorted(
                state["formal_remediation_attempts"],
                key=lambda item: (
                    str(item.get("job_id") or ""),
                    str(item.get("started_at") or ""),
                    str(item.get("attempt_id") or ""),
                ),
            )
            return deepcopy(attempt)

        return self.store.update(mutate)

    def list_formal_remediation_attempts(
        self,
        *,
        job_id: str | None = None,
        fixture_id: str | None = None,
    ) -> list[dict[str, Any]]:
        attempts = self.store.load()["formal_remediation_attempts"]
        if job_id is not None:
            attempts = [item for item in attempts if item.get("job_id") == job_id]
        if fixture_id is not None:
            attempts = [item for item in attempts if item.get("fixture_id") == fixture_id]
        return sorted(
            attempts,
            key=lambda item: (
                str(item.get("job_id") or ""),
                str(item.get("started_at") or ""),
                str(item.get("attempt_id") or ""),
            ),
        )

    def add_alerts(self, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not alerts:
            return []
        state = self.store.load()
        state["alerts"].extend(alerts)
        self.store.save(state)
        return alerts

    def list_alerts(self, match_id: str | None = None) -> list[dict[str, Any]]:
        alerts = self.store.load()["alerts"]
        if match_id is not None:
            alerts = [item for item in alerts if item["match_id"] == match_id]
        return alerts

    def add_scheduler_job_result(self, result: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        state["scheduler_job_results"].append(result)
        self.store.save(state)
        return result

    def list_scheduler_job_results(self) -> list[dict[str, Any]]:
        return self.store.load()["scheduler_job_results"]

    def add_match_day_watch_run(self, run: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        state["match_day_watch_runs"].append(run)
        state["match_day_watch_runs"] = sorted(
            state["match_day_watch_runs"],
            key=lambda item: (
                str(item.get("watch_date") or ""),
                str(item.get("started_at") or ""),
                str(item.get("run_id") or ""),
            ),
        )
        self.store.save(state)
        return run

    def list_match_day_watch_runs(self, watch_date: str | None = None) -> list[dict[str, Any]]:
        runs = self.store.load()["match_day_watch_runs"]
        if watch_date is not None:
            runs = [item for item in runs if item.get("watch_date") == watch_date]
        return sorted(
            runs,
            key=lambda item: (
                str(item.get("watch_date") or ""),
                str(item.get("started_at") or ""),
                str(item.get("run_id") or ""),
            ),
        )

    def latest_match_day_watch_run(self, watch_date: str | None = None) -> dict[str, Any] | None:
        runs = self.list_match_day_watch_runs(watch_date=watch_date)
        return runs[-1] if runs else None

    def add_match_day_watch_event(self, event: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        state["match_day_watch_events"].append(event)
        state["match_day_watch_events"] = sorted(
            state["match_day_watch_events"],
            key=lambda item: (
                str(item.get("fixture_id") or ""),
                str(item.get("created_at") or ""),
                str(item.get("event_id") or ""),
            ),
        )
        self.store.save(state)
        return event

    def list_match_day_watch_events(
        self,
        fixture_id: str | None = None,
        watch_date: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        events = self.store.load()["match_day_watch_events"]
        if fixture_id is not None:
            events = [item for item in events if item.get("fixture_id") == fixture_id]
        if watch_date is not None:
            events = [item for item in events if item.get("watch_date") == watch_date]
        if run_id is not None:
            events = [item for item in events if item.get("run_id") == run_id]
        return sorted(
            events,
            key=lambda item: (
                str(item.get("fixture_id") or ""),
                str(item.get("created_at") or ""),
                str(item.get("event_id") or ""),
            ),
        )

    def latest_match_day_watch_event(self, fixture_id: str) -> dict[str, Any] | None:
        events = self.list_match_day_watch_events(fixture_id=fixture_id)
        return events[-1] if events else None

    def save_pre_match_crawler_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            existing = {
                str(item.get("snapshot_id")): item
                for item in state["pre_match_crawler_snapshots"]
            }
            existing[str(snapshot["snapshot_id"])] = snapshot
            state["pre_match_crawler_snapshots"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("fixture_id") or item.get("match_id") or ""),
                    str(item.get("captured_at") or ""),
                    _capture_window_latest_rank(str(item.get("capture_window") or "")),
                    str(item.get("snapshot_id") or ""),
                ),
            )
            return snapshot

        self.store.update(mutate)
        return snapshot

    def list_pre_match_crawler_snapshots(
        self,
        fixture_id: str | None = None,
        match_id: str | None = None,
    ) -> list[dict[str, Any]]:
        snapshots = self.store.load()["pre_match_crawler_snapshots"]
        if fixture_id is not None:
            snapshots = [item for item in snapshots if item.get("fixture_id") == fixture_id]
        if match_id is not None:
            snapshots = [item for item in snapshots if item.get("match_id") == match_id]
        return sorted(
            snapshots,
            key=lambda item: (
                str(item.get("fixture_id") or item.get("match_id") or ""),
                str(item.get("captured_at") or ""),
                _capture_window_latest_rank(str(item.get("capture_window") or "")),
                str(item.get("snapshot_id") or ""),
            ),
        )

    def latest_pre_match_crawler_snapshot(self, fixture_id: str) -> dict[str, Any] | None:
        snapshots = self.list_pre_match_crawler_snapshots(fixture_id=fixture_id)
        return snapshots[-1] if snapshots else None

    def save_pre_match_graph_run(self, graph_run: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            existing = {
                str(item.get("graph_run_id")): item
                for item in state["pre_match_graph_runs"]
            }
            existing[str(graph_run["graph_run_id"])] = graph_run
            state["pre_match_graph_runs"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("fixture_id") or ""),
                    str(item.get("started_at") or ""),
                    str(item.get("graph_run_id") or ""),
                ),
            )
            return graph_run

        self.store.update(mutate)
        return graph_run

    def list_pre_match_graph_runs(self, fixture_id: str | None = None) -> list[dict[str, Any]]:
        runs = self.store.load()["pre_match_graph_runs"]
        if fixture_id is not None:
            runs = [item for item in runs if item.get("fixture_id") == fixture_id]
        return sorted(
            runs,
            key=lambda item: (
                str(item.get("fixture_id") or ""),
                str(item.get("started_at") or ""),
                str(item.get("graph_run_id") or ""),
            ),
        )

    def get_pre_match_graph_run(self, graph_run_id: str) -> dict[str, Any] | None:
        for run in self.store.load()["pre_match_graph_runs"]:
            if run.get("graph_run_id") == graph_run_id:
                return run
        return None

    def save_crawler_performance_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not records:
            return []

        def mutate(state: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
            existing = {
                str(item.get("record_id")): item
                for item in state["crawler_performance_records"]
            }
            for record in records:
                existing[str(record["record_id"])] = record
            state["crawler_performance_records"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("fixture_id") or ""),
                    str(item.get("captured_at") or ""),
                    str(item.get("target_category") or ""),
                    str(item.get("record_id") or ""),
                ),
            )
            return records

        return self.store.update(mutate)

    def list_crawler_performance_records(
        self,
        *,
        fixture_id: str | None = None,
        skill_version_id: str | None = None,
        target_category: str | None = None,
        source: str | None = None,
        graph_run_id: str | None = None,
        verification_result: str | None = None,
        verified_after_match: bool | None = None,
    ) -> list[dict[str, Any]]:
        records = self.store.load()["crawler_performance_records"]
        if fixture_id is not None:
            records = [item for item in records if item.get("fixture_id") == fixture_id]
        if skill_version_id is not None:
            records = [item for item in records if item.get("skill_version_id") == skill_version_id]
        if target_category is not None:
            records = [item for item in records if item.get("target_category") == target_category]
        if source is not None:
            records = [item for item in records if item.get("source") == source]
        if graph_run_id is not None:
            records = [item for item in records if item.get("graph_run_id") == graph_run_id]
        if verification_result is not None:
            records = [item for item in records if item.get("verification_result") == verification_result]
        if verified_after_match is not None:
            records = [item for item in records if bool(item.get("verified_after_match")) is verified_after_match]
        return sorted(
            records,
            key=lambda item: (
                str(item.get("fixture_id") or ""),
                str(item.get("captured_at") or ""),
                str(item.get("target_category") or ""),
                str(item.get("record_id") or ""),
            ),
        )

    def apply_crawler_performance_verifications(
        self,
        fixture_id: str,
        verifications: list[dict[str, Any]],
        *,
        skill_version_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not verifications:
            return []

        def mutate(state: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
            verification_by_record_id: dict[str, dict[str, Any]] = {}
            for verification in verifications:
                for record_id in list(verification.get("record_ids") or []):
                    verification_by_record_id[str(record_id)] = verification
            updated: list[dict[str, Any]] = []
            for record in state["crawler_performance_records"]:
                if record.get("fixture_id") != fixture_id:
                    continue
                if skill_version_id is not None and record.get("skill_version_id") != skill_version_id:
                    continue
                verification = verification_by_record_id.get(str(record.get("record_id") or ""))
                if verification is None:
                    continue
                history = list(record.get("verification_history") or [])
                history.append(
                    {
                        "reviewed_at": utc_now(),
                        "verification_result": verification["verification_result"],
                        "verified_by": verification.get("verified_by"),
                        "verified_from": verification.get("verified_from"),
                        "notes": verification.get("notes"),
                        "evidence": list(verification.get("evidence") or []),
                    }
                )
                record["verified_after_match"] = True
                record["verification_result"] = verification["verification_result"]
                record["verification_notes"] = verification.get("notes")
                record["verification_evidence"] = list(verification.get("evidence") or [])
                record["verification_source"] = verification.get("verified_from")
                record["verified_by"] = verification.get("verified_by")
                record["verified_at"] = utc_now()
                record["verification_history"] = history
                updated.append(record)
            return updated

        return self.store.update(mutate)

    def save_crawler_skill_versions(self, versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not versions:
            return []

        def mutate(state: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
            existing = {
                str(item.get("skill_version_id")): item
                for item in state["crawler_skill_versions"]
            }
            approved_ids = {
                str(item["skill_version_id"])
                for item in versions
                if str(item.get("status") or "") == "approved"
            }
            if approved_ids:
                for skill_version_id, item in existing.items():
                    if item.get("status") == "approved" and skill_version_id not in approved_ids:
                        item["status"] = "superseded"
                        item["updated_at"] = utc_now()
            for version in versions:
                skill_version_id = str(version["skill_version_id"])
                previous = existing.get(skill_version_id, {})
                existing[skill_version_id] = {
                    **previous,
                    **version,
                }
            state["crawler_skill_versions"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("created_at") or ""),
                    str(item.get("skill_version_id") or ""),
                ),
            )
            return versions

        return self.store.update(mutate)

    def list_crawler_skill_versions(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        versions = self.store.load()["crawler_skill_versions"]
        if status is not None:
            versions = [item for item in versions if item.get("status") == status]
        ordered = sorted(
            versions,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("skill_version_id") or ""),
            ),
        )
        if limit is not None and limit > 0:
            return ordered[-limit:]
        return ordered

    def get_crawler_skill_version(self, skill_version_id: str) -> dict[str, Any] | None:
        for version in self.store.load()["crawler_skill_versions"]:
            if version.get("skill_version_id") == skill_version_id:
                return version
        return None

    def latest_crawler_skill_version(self, *, status: str | None = None) -> dict[str, Any] | None:
        versions = self.list_crawler_skill_versions(status=status)
        return versions[-1] if versions else None

    def save_crawler_skill_update_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            existing = {
                str(item.get("proposal_id")): item
                for item in state["crawler_skill_update_proposals"]
            }
            previous = existing.get(str(proposal["proposal_id"]), {})
            existing[str(proposal["proposal_id"])] = {
                **previous,
                **proposal,
            }
            state["crawler_skill_update_proposals"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("created_at") or ""),
                    str(item.get("proposal_id") or ""),
                ),
            )
            return existing[str(proposal["proposal_id"])]

        return self.store.update(mutate)

    def list_crawler_skill_update_proposals(
        self,
        *,
        status: str | None = None,
        base_skill_version_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        proposals = self.store.load()["crawler_skill_update_proposals"]
        if status is not None:
            proposals = [item for item in proposals if item.get("status") == status]
        if base_skill_version_id is not None:
            proposals = [item for item in proposals if item.get("base_skill_version_id") == base_skill_version_id]
        ordered = sorted(
            proposals,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("proposal_id") or ""),
            ),
            reverse=True,
        )
        if limit is not None and limit > 0:
            return ordered[:limit]
        return ordered

    def get_crawler_skill_update_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        for proposal in self.store.load()["crawler_skill_update_proposals"]:
            if proposal.get("proposal_id") == proposal_id:
                return proposal
        return None

    def review_crawler_skill_update_proposal(
        self,
        proposal_id: str,
        *,
        expected_status: str,
        status: str,
        review_action: str,
        review_reason: str,
        review_actor: dict[str, Any],
        published_skill_version_builder: Callable[[dict[str, Any], str, str], dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
            updated: dict[str, Any] | None = None
            now = utc_now()
            for proposal in state["crawler_skill_update_proposals"]:
                if proposal.get("proposal_id") != proposal_id:
                    continue
                if proposal.get("status") != expected_status:
                    return None
                published_skill_version_id: str | None = None
                published_skill_version: dict[str, Any] | None = None
                if published_skill_version_builder is not None:
                    existing_versions = {
                        str(item.get("skill_version_id")): item
                        for item in state["crawler_skill_versions"]
                    }
                    base_version_id = str(proposal.get("base_skill_version_id") or "")
                    current_approved_version_id = next(
                        (
                            str(item.get("skill_version_id") or "")
                            for item in reversed(state["crawler_skill_versions"])
                            if item.get("status") == "approved"
                        ),
                        "",
                    )
                    if current_approved_version_id and current_approved_version_id != base_version_id:
                        return {
                            "error": "crawler_skill_update_proposal_stale_base_version",
                            "proposal_id": proposal_id,
                            "base_skill_version_id": base_version_id,
                            "current_approved_skill_version_id": current_approved_version_id,
                        }
                    base_version = existing_versions.get(base_version_id)
                    if base_version is None:
                        raise ValueError("crawler_skill_version_not_found")
                    published_skill_version_id = _next_crawler_skill_version_id(existing_versions.values())
                    published_skill_version = published_skill_version_builder(
                        deepcopy(base_version),
                        published_skill_version_id,
                        now,
                    )
                    if str(published_skill_version.get("skill_version_id") or "") != published_skill_version_id:
                        raise ValueError("crawler_skill_version_id_mismatch")
                history = list(proposal.get("review_history") or [])
                history.append(
                    {
                        "reviewed_at": now,
                        "action": review_action,
                        "from_status": expected_status,
                        "to_status": status,
                        "actor": review_actor,
                        "reason": review_reason,
                        "published_skill_version_id": published_skill_version_id,
                    }
                )
                proposal["status"] = status
                proposal["updated_at"] = now
                proposal["latest_review_action"] = review_action
                proposal["latest_review_reason"] = review_reason
                proposal["latest_review_actor"] = review_actor
                proposal["latest_reviewed_at"] = now
                proposal["review_history"] = history
                if published_skill_version_id is not None:
                    proposal["published_skill_version_id"] = published_skill_version_id
                if published_skill_version is not None:
                    existing_versions = {
                        str(item.get("skill_version_id")): item
                        for item in state["crawler_skill_versions"]
                    }
                    approved_id = str(published_skill_version["skill_version_id"])
                    for skill_version_id, item in existing_versions.items():
                        if item.get("status") == "approved" and skill_version_id != approved_id:
                            item["status"] = "superseded"
                            item["updated_at"] = now
                    previous = existing_versions.get(approved_id, {})
                    existing_versions[approved_id] = {
                        **previous,
                        **published_skill_version,
                    }
                    state["crawler_skill_versions"] = sorted(
                        existing_versions.values(),
                        key=lambda item: (
                            str(item.get("created_at") or ""),
                            str(item.get("skill_version_id") or ""),
                        ),
                    )
                updated = proposal
                break
            if updated is None:
                return None
            return {
                "proposal": updated,
                "published_skill_version": (
                    None
                    if updated.get("published_skill_version_id") is None
                    else next(
                        (
                            item
                            for item in state["crawler_skill_versions"]
                            if item.get("skill_version_id") == updated.get("published_skill_version_id")
                        ),
                        None,
                    )
                ),
            }

        return self.store.update(mutate)

    def save_pre_match_news_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            existing = {
                str(item.get("snapshot_id")): item
                for item in state["pre_match_news_snapshots"]
            }
            existing[str(snapshot["snapshot_id"])] = snapshot
            state["pre_match_news_snapshots"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("fixture_id") or item.get("match_id") or ""),
                    str(item.get("captured_at") or ""),
                    _capture_window_latest_rank(str(item.get("capture_window") or "")),
                    str(item.get("snapshot_id") or ""),
                ),
            )
            return snapshot

        self.store.update(mutate)
        return snapshot

    def list_pre_match_news_snapshots(
        self,
        fixture_id: str | None = None,
        match_id: str | None = None,
    ) -> list[dict[str, Any]]:
        snapshots = self.store.load()["pre_match_news_snapshots"]
        if fixture_id is not None:
            snapshots = [item for item in snapshots if item.get("fixture_id") == fixture_id]
        if match_id is not None:
            snapshots = [item for item in snapshots if item.get("match_id") == match_id]
        return sorted(
            snapshots,
            key=lambda item: (
                str(item.get("fixture_id") or item.get("match_id") or ""),
                str(item.get("captured_at") or ""),
                _capture_window_latest_rank(str(item.get("capture_window") or "")),
                str(item.get("snapshot_id") or ""),
            ),
        )

    def latest_pre_match_news_snapshot(self, fixture_id: str) -> dict[str, Any] | None:
        snapshots = self.list_pre_match_news_snapshots(fixture_id=fixture_id)
        return snapshots[-1] if snapshots else None

    def append_live_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        state["live_snapshots"].append(snapshot)
        self.store.save(state)
        return snapshot

    def append_live_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not events:
            return []
        state = self.store.load()
        state["live_events"].extend(events)
        self.store.save(state)
        return events

    def list_live_snapshots(self, match_id: str) -> list[dict[str, Any]]:
        return [item for item in self.store.load()["live_snapshots"] if item["match_id"] == match_id]

    def latest_live_snapshot(self, match_id: str) -> dict[str, Any] | None:
        snapshots = self.list_live_snapshots(match_id)
        if not snapshots:
            return None
        return sorted(snapshots, key=lambda item: item["captured_at"])[-1]

    def list_live_events(self, match_id: str) -> list[dict[str, Any]]:
        return [item for item in self.store.load()["live_events"] if item["match_id"] == match_id]

    def append_live_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        existing = state["live_signals"]
        signature = self._live_signal_signature(signal)
        for item in existing:
            if self._live_signal_signature(item) == signature:
                return item
        existing.append(signal)
        self.store.save(state)
        return signal

    def list_live_signals(self, match_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        signals = [item for item in self.store.load()["live_signals"] if item["match_id"] == match_id]
        ordered = sorted(signals, key=lambda item: item.get("created_at") or "")
        if limit is not None and limit > 0:
            return ordered[-limit:]
        return ordered

    def latest_live_signal(self, match_id: str) -> dict[str, Any] | None:
        signals = self.list_live_signals(match_id, limit=1)
        return signals[-1] if signals else None

    def add_live_alerts(self, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not alerts:
            return []
        state = self.store.load()
        state["live_alerts"].extend(alerts)
        self.store.save(state)
        return alerts

    def list_live_alerts(self, match_id: str | None = None) -> list[dict[str, Any]]:
        alerts = self.store.load()["live_alerts"]
        if match_id is not None:
            alerts = [item for item in alerts if item["match_id"] == match_id]
        return sorted(alerts, key=lambda item: item.get("created_at") or "")

    def latest_live_alert(
        self,
        match_id: str,
        alert_type: str,
        alert_family: str = "live_signal",
    ) -> dict[str, Any] | None:
        alerts = [
            item
            for item in self.list_live_alerts(match_id=match_id)
            if item.get("alert_family") == alert_family and item.get("alert_type") == alert_type
        ]
        return alerts[-1] if alerts else None

    def save_post_match_learning_package(self, package: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            existing = {
                str(item.get("learning_package_id")): item
                for item in state["post_match_learning_packages"]
            }
            existing[str(package["learning_package_id"])] = package
            state["post_match_learning_packages"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("fixture_id") or item.get("match_id") or ""),
                    str(item.get("created_at") or ""),
                    str(item.get("learning_package_id") or ""),
                ),
            )
            return package

        self.store.update(mutate)
        return package

    def list_post_match_learning_packages(
        self,
        fixture_id: str | None = None,
        match_id: str | None = None,
    ) -> list[dict[str, Any]]:
        packages = self.store.load()["post_match_learning_packages"]
        if fixture_id is not None:
            packages = [item for item in packages if item.get("fixture_id") == fixture_id]
        if match_id is not None:
            packages = [item for item in packages if item.get("match_id") == match_id]
        return sorted(
            packages,
            key=lambda item: (
                str(item.get("fixture_id") or item.get("match_id") or ""),
                str(item.get("created_at") or ""),
                str(item.get("learning_package_id") or ""),
            ),
        )

    def get_post_match_learning_package(self, learning_package_id: str) -> dict[str, Any] | None:
        for package in self.store.load()["post_match_learning_packages"]:
            if package.get("learning_package_id") == learning_package_id:
                return package
        return None

    def latest_post_match_learning_package(self, fixture_id: str) -> dict[str, Any] | None:
        packages = self.list_post_match_learning_packages(fixture_id=fixture_id)
        return packages[-1] if packages else None

    def save_post_match_review(self, review: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            existing = {
                str(item.get("review_id")): item
                for item in state["post_match_reviews"]
            }
            existing[str(review["review_id"])] = review
            state["post_match_reviews"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("created_at") or ""),
                    str(item.get("review_id") or ""),
                ),
            )
            return review

        self.store.update(mutate)
        return review

    def list_post_match_reviews(self, match_id: str | None = None) -> list[dict[str, Any]]:
        reviews = self.store.load()["post_match_reviews"]
        if match_id is not None:
            reviews = [item for item in reviews if item["match_id"] == match_id]
        return sorted(reviews, key=lambda item: item.get("created_at") or "")

    def latest_post_match_review(self, match_id: str) -> dict[str, Any] | None:
        reviews = self.list_post_match_reviews(match_id=match_id)
        return reviews[-1] if reviews else None

    def post_match_review_by_learning_package(self, learning_package_id: str) -> dict[str, Any] | None:
        reviews = [
            item
            for item in self.store.load()["post_match_reviews"]
            if item.get("post_match_learning_package_id") == learning_package_id
        ]
        return sorted(reviews, key=lambda item: item.get("created_at") or "")[-1] if reviews else None

    def save_post_match_review_once_by_learning_package(
        self,
        review: dict[str, Any],
        learning_package_id: str,
    ) -> tuple[dict[str, Any], bool]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], bool]:
            existing_reviews = [
                item
                for item in state["post_match_reviews"]
                if item.get("post_match_learning_package_id") == learning_package_id
            ]
            if existing_reviews:
                existing = sorted(existing_reviews, key=lambda item: item.get("created_at") or "")[-1]
                return existing, False
            state["post_match_reviews"].append(review)
            state["post_match_reviews"] = sorted(
                state["post_match_reviews"],
                key=lambda item: (
                    str(item.get("created_at") or ""),
                    str(item.get("review_id") or ""),
                ),
            )
            return review, True

        return self.store.update(mutate)

    def save_governance_proposals(self, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not proposals:
            return []
        state = self.store.load()
        now = utc_now()
        existing = {
            self._governance_proposal_signature(item): item
            for item in state["governance_proposals"]
        }
        saved: list[dict[str, Any]] = []
        for proposal in proposals:
            signature = self._governance_proposal_signature(proposal)
            previous = existing.get(signature, {})
            record = {
                **previous,
                **proposal,
                "proposal_record_id": previous.get("proposal_record_id", proposal["proposal_record_id"]),
                "created_at": previous.get("created_at", proposal.get("created_at", now)),
                "updated_at": proposal.get("updated_at", now),
            }
            existing[signature] = record
            saved.append(record)
        state["governance_proposals"] = sorted(
            existing.values(),
            key=lambda item: (
                str(item.get("match_id") or ""),
                str(item.get("source_review_id") or ""),
                str(item.get("created_at") or ""),
                str(item.get("proposal_record_id") or ""),
            ),
        )
        self.store.save(state)
        return saved

    def save_governance_capture_bundle(
        self,
        proposals: list[dict[str, Any]],
        versions: list[dict[str, Any]],
        audit_logs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not proposals:
            return []
        def mutate(state: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
            existing_proposals = {
                self._governance_proposal_signature(item): item
                for item in state["governance_proposals"]
            }
            requested_signatures = [self._governance_proposal_signature(item) for item in proposals]
            existing_requested = [
                existing_proposals[signature]
                for signature in requested_signatures
                if signature in existing_proposals
            ]
            if existing_requested:
                return sorted(
                    existing_requested,
                    key=lambda item: (
                        str(item.get("created_at") or ""),
                        str(item.get("proposal_record_id") or ""),
                    ),
                )

            existing_version_signatures = {
                self._governance_proposal_version_signature(item)
                for item in state["governance_proposal_versions"]
            }
            duplicate_versions = [
                version
                for version in versions
                if self._governance_proposal_version_signature(version) in existing_version_signatures
            ]
            if duplicate_versions:
                raise ValueError("governance_proposal_version_already_exists")

            existing_audit_ids = {
                str(item.get("audit_log_id"))
                for item in state["governance_audit_logs"]
            }
            duplicate_audit_logs = [
                log
                for log in audit_logs
                if str(log.get("audit_log_id")) in existing_audit_ids
            ]
            if duplicate_audit_logs:
                raise ValueError("governance_audit_log_already_exists")

            state["governance_proposals"] = sorted(
                [*state["governance_proposals"], *proposals],
                key=lambda item: (
                    str(item.get("match_id") or ""),
                    str(item.get("source_review_id") or ""),
                    str(item.get("created_at") or ""),
                    str(item.get("proposal_record_id") or ""),
                ),
            )
            state["governance_proposal_versions"] = sorted(
                [*state["governance_proposal_versions"], *versions],
                key=lambda item: (
                    str(item.get("proposal_record_id") or ""),
                    int(item.get("version_number") or 0),
                ),
            )
            state["governance_audit_logs"] = sorted(
                [*state["governance_audit_logs"], *audit_logs],
                key=lambda item: (
                    str(item.get("proposal_record_id") or ""),
                    int(item.get("sequence_number") or 0),
                    str(item.get("created_at") or ""),
                    str(item.get("audit_log_id") or ""),
                ),
            )
            return proposals

        return self.store.update(mutate)

    def list_governance_proposals(
        self,
        *,
        match_id: str | None = None,
        source_review_id: str | None = None,
        status: str | None = None,
        proposal_family: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        proposals = self.store.load()["governance_proposals"]
        if match_id is not None:
            proposals = [item for item in proposals if item.get("match_id") == match_id]
        if source_review_id is not None:
            proposals = [item for item in proposals if item.get("source_review_id") == source_review_id]
        if status is not None:
            proposals = [item for item in proposals if item.get("status") == status]
        if proposal_family is not None:
            proposals = [item for item in proposals if item.get("proposal_family") == proposal_family]
        ordered = sorted(
            proposals,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("proposal_record_id") or ""),
            ),
            reverse=True,
        )
        if limit is not None and limit > 0:
            return ordered[:limit]
        return ordered

    def get_governance_proposal(self, proposal_record_id: str) -> dict[str, Any] | None:
        for proposal in self.store.load()["governance_proposals"]:
            if proposal.get("proposal_record_id") == proposal_record_id:
                return proposal
        return None

    def update_governance_proposal_status(
        self,
        proposal_record_id: str,
        *,
        status: str,
        latest_review_action: str,
        latest_review_reason: str,
        latest_review_actor: dict[str, Any],
    ) -> dict[str, Any] | None:
        state = self.store.load()
        now = utc_now()
        updated: dict[str, Any] | None = None
        for item in state["governance_proposals"]:
            if item.get("proposal_record_id") == proposal_record_id:
                item["status"] = status
                item["updated_at"] = now
                item["latest_review_action"] = latest_review_action
                item["latest_review_reason"] = latest_review_reason
                item["latest_review_actor"] = latest_review_actor
                item["latest_reviewed_at"] = now
                updated = item
                break
        if updated is not None:
            self.store.save(state)
        return updated

    def review_governance_proposal(
        self,
        proposal_record_id: str,
        *,
        expected_status: str,
        status: str,
        latest_review_action: str,
        latest_review_reason: str,
        latest_review_actor: dict[str, Any],
        audit_log_id: str,
    ) -> dict[str, Any] | None:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
            now = utc_now()
            updated: dict[str, Any] | None = None
            for item in state["governance_proposals"]:
                if item.get("proposal_record_id") == proposal_record_id:
                    if item.get("status") != expected_status:
                        return None
                    item["status"] = status
                    item["updated_at"] = now
                    item["latest_review_action"] = latest_review_action
                    item["latest_review_reason"] = latest_review_reason
                    item["latest_review_actor"] = latest_review_actor
                    item["latest_reviewed_at"] = now
                    updated = item
                    break
            if updated is None:
                return None

            existing_audit_ids = {
                str(item.get("audit_log_id"))
                for item in state["governance_audit_logs"]
            }
            if audit_log_id in existing_audit_ids:
                raise ValueError("governance_audit_log_already_exists")
            existing_logs = [
                item
                for item in state["governance_audit_logs"]
                if item.get("proposal_record_id") == proposal_record_id
            ]
            state["governance_audit_logs"].append(
                {
                    "audit_log_id": audit_log_id,
                    "proposal_record_id": proposal_record_id,
                    "sequence_number": len(existing_logs) + 1,
                    "created_at": now,
                    "action": latest_review_action,
                    "from_status": expected_status,
                    "to_status": status,
                    "actor": latest_review_actor,
                    "reason": latest_review_reason,
                    "version_number": int(updated.get("current_version_number") or 1),
                    "metadata": {
                        "source_review_id": str(updated["source_review_id"]),
                        "source_proposal_id": str(updated["source_proposal_id"]),
                    },
                }
            )
            state["governance_audit_logs"] = sorted(
                state["governance_audit_logs"],
                key=lambda item: (
                    str(item.get("proposal_record_id") or ""),
                    int(item.get("sequence_number") or 0),
                    str(item.get("created_at") or ""),
                    str(item.get("audit_log_id") or ""),
                ),
            )
            return updated

        return self.store.update(mutate)

    def save_governance_proposal_versions(self, versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not versions:
            return []
        state = self.store.load()
        existing_signatures = {
            self._governance_proposal_version_signature(item)
            for item in state["governance_proposal_versions"]
        }
        for version in versions:
            signature = self._governance_proposal_version_signature(version)
            if signature in existing_signatures:
                raise ValueError("governance_proposal_version_already_exists")
            existing_signatures.add(signature)
            state["governance_proposal_versions"].append(version)
        state["governance_proposal_versions"] = sorted(
            state["governance_proposal_versions"],
            key=lambda item: (
                str(item.get("proposal_record_id") or ""),
                int(item.get("version_number") or 0),
            ),
        )
        self.store.save(state)
        return versions

    def list_governance_proposal_versions(self, proposal_record_id: str) -> list[dict[str, Any]]:
        versions = [
            item
            for item in self.store.load()["governance_proposal_versions"]
            if item.get("proposal_record_id") == proposal_record_id
        ]
        return sorted(versions, key=lambda item: int(item.get("version_number") or 0))

    def append_governance_audit_logs(self, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not logs:
            return []
        state = self.store.load()
        existing = {
            str(item.get("audit_log_id")): item
            for item in state["governance_audit_logs"]
        }
        for log in logs:
            audit_log_id = str(log["audit_log_id"])
            if audit_log_id in existing:
                raise ValueError("governance_audit_log_already_exists")
            existing[audit_log_id] = log
        state["governance_audit_logs"] = sorted(
            existing.values(),
            key=lambda item: (
                str(item.get("proposal_record_id") or ""),
                int(item.get("sequence_number") or 0),
                str(item.get("created_at") or ""),
                str(item.get("audit_log_id") or ""),
            ),
        )
        self.store.save(state)
        return logs

    def list_governance_audit_logs(self, proposal_record_id: str) -> list[dict[str, Any]]:
        logs = [
            item
            for item in self.store.load()["governance_audit_logs"]
            if item.get("proposal_record_id") == proposal_record_id
        ]
        return sorted(
            logs,
            key=lambda item: (
                int(item.get("sequence_number") or 0),
                str(item.get("created_at") or ""),
                str(item.get("audit_log_id") or ""),
            ),
        )

    def save_weight_candidate_version(self, candidate: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            existing = {
                str(item.get("weight_version_id")): item
                for item in state["weight_candidate_versions"]
            }
            existing[str(candidate["weight_version_id"])] = candidate
            state["weight_candidate_versions"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("created_at") or ""),
                    str(item.get("weight_version_id") or ""),
                ),
            )
            return candidate

        self.store.update(mutate)
        return candidate

    def list_weight_candidate_versions(
        self,
        proposal_record_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        candidates = self.store.load()["weight_candidate_versions"]
        if proposal_record_id is not None:
            candidates = [
                item for item in candidates
                if proposal_record_id in list(item.get("source_governance_proposal_ids") or [])
            ]
        if status is not None:
            candidates = [item for item in candidates if item.get("status") == status]
        return sorted(
            candidates,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("weight_version_id") or ""),
            ),
        )

    def latest_weight_candidate_for_proposal(self, proposal_record_id: str) -> dict[str, Any] | None:
        candidates = self.list_weight_candidate_versions(proposal_record_id=proposal_record_id)
        return candidates[-1] if candidates else None

    def save_calibration_report(self, report: dict[str, Any]) -> dict[str, Any]:
        def mutate(state: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
            existing = {
                str(item.get("calibration_report_id")): item
                for item in state["calibration_reports"]
            }
            existing[str(report["calibration_report_id"])] = report
            state["calibration_reports"] = sorted(
                existing.values(),
                key=lambda item: (
                    str(item.get("created_at") or ""),
                    str(item.get("calibration_report_id") or ""),
                ),
            )
            return report

        self.store.update(mutate)
        return report

    def list_calibration_reports(self, weight_version_id: str | None = None) -> list[dict[str, Any]]:
        reports = self.store.load()["calibration_reports"]
        if weight_version_id is not None:
            reports = [item for item in reports if item.get("weight_version_id") == weight_version_id]
        return sorted(
            reports,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("calibration_report_id") or ""),
            ),
        )

    def save_crawler_contexts(self, contexts: list[dict[str, Any]]) -> int:
        if not contexts:
            return 0
        state = self.store.load()
        existing = {
            str(item.get("context_id")): item
            for item in state["crawler_contexts"]
        }
        for context in contexts:
            existing[str(context["context_id"])] = context
        state["crawler_contexts"] = sorted(
            existing.values(),
            key=lambda item: (
                str(item.get("match_id") or ""),
                str(item.get("target_type") or ""),
                str(item.get("generated_at") or ""),
                str(item.get("context_id") or ""),
            ),
        )
        self.store.save(state)
        return len(contexts)

    def list_crawler_contexts(
        self,
        match_id: str | None = None,
        target_type: str | None = None,
    ) -> list[dict[str, Any]]:
        contexts = self.store.load()["crawler_contexts"]
        if match_id is not None:
            contexts = [item for item in contexts if item.get("match_id") == match_id]
        if target_type is not None:
            contexts = [item for item in contexts if item.get("target_type") == target_type]
        return sorted(contexts, key=lambda item: item.get("generated_at") or "")

    def latest_crawler_context(self, match_id: str, target_type: str) -> dict[str, Any] | None:
        contexts = self.list_crawler_contexts(match_id=match_id, target_type=target_type)
        return contexts[-1] if contexts else None

    def get_crawler_context(self, context_id: str) -> dict[str, Any] | None:
        for context in self.store.load()["crawler_contexts"]:
            if context.get("context_id") == context_id:
                return context
        return None

    def upsert_sportradar_entity_mappings(self, mappings: list[dict[str, Any]]) -> int:
        if not mappings:
            return 0
        state = self.store.load()
        now = utc_now()
        existing = {
            self._sportradar_mapping_signature(item): item
            for item in state["sportradar_entity_mappings"]
        }
        for mapping in mappings:
            record = {
                "created_at": now,
                "updated_at": now,
                **mapping,
            }
            signature = self._sportradar_mapping_signature(record)
            previous = existing.get(signature, {})
            existing[signature] = {
                **previous,
                **record,
                "created_at": previous.get("created_at", record["created_at"]),
                "updated_at": now,
            }
        state["sportradar_entity_mappings"] = sorted(
            existing.values(),
            key=lambda item: (
                str(item.get("internal_match_id") or ""),
                str(item.get("team_name") or ""),
                str(item.get("competitor_id") or ""),
            ),
        )
        self.store.save(state)
        return len(mappings)

    def list_sportradar_entity_mappings(
        self,
        match_id: str | None = None,
        season_id: str | None = None,
    ) -> list[dict[str, Any]]:
        mappings = self.store.load()["sportradar_entity_mappings"]
        if match_id is not None:
            mappings = [item for item in mappings if item.get("internal_match_id") == match_id]
        if season_id is not None:
            mappings = [item for item in mappings if item.get("season_id") == season_id]
        return mappings

    def save_sportradar_research_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        state["sportradar_research_snapshots"].append(snapshot)
        self.store.save(state)
        return snapshot

    def list_sportradar_research_snapshots(
        self,
        match_id: str | None = None,
        season_id: str | None = None,
    ) -> list[dict[str, Any]]:
        snapshots = self.store.load()["sportradar_research_snapshots"]
        if match_id is not None:
            snapshots = [
                item for item in snapshots
                if (item.get("scope") if isinstance(item.get("scope"), dict) else {}).get("match_id") == match_id
            ]
        if season_id is not None:
            snapshots = [
                item for item in snapshots
                if (item.get("scope") if isinstance(item.get("scope"), dict) else {}).get("season_id") == season_id
            ]
        return sorted(snapshots, key=lambda item: item.get("generated_at") or "")

    def get_sportradar_research_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        for snapshot in self.store.load()["sportradar_research_snapshots"]:
            if snapshot.get("snapshot_id") == snapshot_id:
                return snapshot
        return None

    def latest_sportradar_research_snapshot(
        self,
        match_id: str | None = None,
        season_id: str | None = None,
    ) -> dict[str, Any] | None:
        snapshots = self.list_sportradar_research_snapshots(match_id=match_id, season_id=season_id)
        return snapshots[-1] if snapshots else None

    @staticmethod
    def _live_signal_signature(signal: dict[str, Any]) -> tuple[Any, ...]:
        return (
            signal.get("match_id"),
            signal.get("minute"),
            signal.get("main_signal"),
            round(float(signal.get("confidence") or 0), 4),
            tuple(signal.get("reason_codes") or []),
            signal.get("window_start_minute"),
            signal.get("window_end_minute"),
        )

    @staticmethod
    def _sportradar_mapping_signature(mapping: dict[str, Any]) -> tuple[Any, ...]:
        return (
            mapping.get("internal_match_id"),
            mapping.get("team_name"),
            mapping.get("season_id"),
            mapping.get("competitor_id"),
            mapping.get("sport_event_id"),
        )

    @staticmethod
    def _governance_proposal_signature(proposal: dict[str, Any]) -> tuple[Any, ...]:
        return (
            proposal.get("source_review_id"),
            proposal.get("proposal_family"),
            proposal.get("source_proposal_id"),
        )

    @staticmethod
    def _governance_proposal_version_signature(version: dict[str, Any]) -> tuple[Any, ...]:
        return (
            version.get("proposal_record_id"),
            int(version.get("version_number") or 0),
        )


def _capture_window_latest_rank(capture_window: str) -> int:
    if capture_window.startswith("T-") and capture_window.endswith("m"):
        try:
            minutes = int(capture_window[2:-1])
        except ValueError:
            return -9999
        return -minutes
    return -9999


def _merge_sorted_strings(*values: Any) -> list[str]:
    merged: list[str] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            text = str(item or "").strip()
            if text and text not in merged:
                merged.append(text)
    return sorted(merged)


def _merge_repair_actions(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for collection in (existing, incoming):
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "")
            capture_window = str(item.get("capture_window") or "")
            if not action:
                continue
            key = (action, capture_window)
            current = merged.get(key, {})
            merged[key] = {
                **current,
                **item,
                "reason_codes": _merge_sorted_strings(
                    current.get("reason_codes"),
                    item.get("reason_codes"),
                ),
            }
    return [
        merged[key]
        for key in sorted(merged)
    ]


def _next_crawler_skill_version_id(versions: Any) -> str:
    numeric_suffixes: list[int] = []
    for version in versions:
        skill_version_id = str((version or {}).get("skill_version_id") or "")
        suffix = skill_version_id.removeprefix("crawler_skill_v")
        if suffix.isdigit():
            numeric_suffixes.append(int(suffix))
    next_value = max(numeric_suffixes, default=0) + 1
    return f"crawler_skill_v{next_value:03d}"
