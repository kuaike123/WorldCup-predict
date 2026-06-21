from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar


EMPTY_STATE: dict[str, list[dict[str, Any]]] = {
    "matches": [],
    "odds_snapshots": [],
    "subscriptions": [],
    "agent_reports": [],
    "push_logs": [],
    "alerts": [],
    "scheduler_job_results": [],
    "match_day_watch_runs": [],
    "match_day_watch_events": [],
    "pre_match_news_snapshots": [],
    "live_snapshots": [],
    "live_events": [],
    "live_signals": [],
    "live_alerts": [],
    "post_match_learning_packages": [],
    "post_match_reviews": [],
    "governance_proposals": [],
    "governance_proposal_versions": [],
    "governance_audit_logs": [],
    "weight_candidate_versions": [],
    "calibration_reports": [],
    "crawler_contexts": [],
    "pre_match_crawler_snapshots": [],
    "pre_match_graph_runs": [],
    "crawler_performance_records": [],
    "crawler_skill_versions": [],
    "crawler_skill_update_proposals": [],
    "sportradar_research_snapshots": [],
    "sportradar_entity_mappings": [],
    "formal_remediation_jobs": [],
    "formal_remediation_attempts": [],
}


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}
T = TypeVar("T")


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = _path_lock(path)

    def load(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            return self._load_unlocked()

    def save(self, state: dict[str, list[dict[str, Any]]]) -> None:
        with self._lock:
            self._save_unlocked(state)

    def update(self, mutator: Callable[[dict[str, list[dict[str, Any]]]], T]) -> T:
        with self._lock:
            state = self._load_unlocked()
            result = mutator(state)
            self._save_unlocked(state)
            return result

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock:
            yield

    def _load_unlocked(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return deepcopy(EMPTY_STATE)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        state = deepcopy(EMPTY_STATE)
        for key in state:
            if isinstance(data.get(key), list):
                state[key] = data[key]
        return state

    def _save_unlocked(self, state: dict[str, list[dict[str, Any]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        # ponytail: short retry handles transient Windows file locks; add a stronger lock only if this still flakes.
        for attempt in range(5):
            try:
                temp_path.replace(self.path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))


def _path_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[resolved] = lock
        return lock
