"""Revisioned canary model rollout with measurable gates and deterministic routing."""

import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

TRAFFIC_STEPS = (5, 10, 25, 50, 100)


def _passes(candidate: dict, baseline: dict) -> tuple[bool, list[str]]:
    failures = []
    if candidate["success_rate"] < baseline["success_rate"] - 0.02:
        failures.append("success_rate_regressed")
    if candidate["error_rate"] > baseline["error_rate"] + 0.01:
        failures.append("error_rate_regressed")
    if candidate["p95_latency_ms"] > max(baseline["p95_latency_ms"] * 1.2, 1):
        failures.append("p95_latency_regressed")
    if candidate["human_rejection_rate"] > baseline["human_rejection_rate"] + 0.03:
        failures.append("human_rejection_rate_regressed")
    return not failures, failures


class ModelRolloutRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {"revision": 0, "status": "inactive", "traffic_percent": 0, "events": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, state: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return state

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = None
        for _ in range(100):
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                time.sleep(0.01)
        if descriptor is None:
            raise RuntimeError("rollout_lock_timeout")
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            yield
        finally:
            os.close(descriptor)
            lock_path.unlink(missing_ok=True)

    def start(
        self,
        *,
        expected_revision: int,
        baseline_model: str,
        candidate_model: str,
        baseline_metrics: dict,
        candidate_metrics: dict,
        reviewer: str,
    ) -> dict:
        with self._locked():
            passed, failures = _passes(candidate_metrics, baseline_metrics)
            if not passed:
                raise ValueError("rollout_quality_gate_failed:" + ",".join(failures))
            current = self.load()
            if current["revision"] != expected_revision:
                raise ValueError("rollout_revision_conflict")
            state = {
                "revision": current["revision"] + 1,
                "status": "canary",
                "baseline_model": baseline_model,
                "candidate_model": candidate_model,
                "traffic_percent": TRAFFIC_STEPS[0],
                "baseline_metrics": baseline_metrics,
                "latest_candidate_metrics": candidate_metrics,
                "events": [
                    *current.get("events", [])[-99:],
                    {
                        "type": "rollout_started",
                        "reviewer": reviewer,
                        "traffic_percent": TRAFFIC_STEPS[0],
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                ],
            }
            return self._save(state)

    def advance(self, expected_revision: int, metrics: dict, reviewer: str) -> dict:
        with self._locked():
            state = self.load()
            if state["revision"] != expected_revision:
                raise ValueError("rollout_revision_conflict")
            if state["status"] not in {"canary", "promoting"}:
                raise ValueError("rollout_not_active")
            passed, failures = _passes(metrics, state["baseline_metrics"])
            if not passed:
                return self._rollback_state(state, reviewer, ",".join(failures), metrics)
            index = TRAFFIC_STEPS.index(state["traffic_percent"])
            next_traffic = TRAFFIC_STEPS[min(index + 1, len(TRAFFIC_STEPS) - 1)]
            state.update({
                "revision": state["revision"] + 1,
                "status": "active" if next_traffic == 100 else "promoting",
                "traffic_percent": next_traffic,
                "latest_candidate_metrics": metrics,
            })
            state["events"].append({
                "type": "rollout_advanced",
                "reviewer": reviewer,
                "traffic_percent": next_traffic,
                "created_at": datetime.now(UTC).isoformat(),
            })
            return self._save(state)

    def _rollback_state(
        self, state: dict, reviewer: str, reason: str, metrics: dict | None = None
    ) -> dict:
        state.update({
            "revision": state["revision"] + 1,
            "status": "rolled_back",
            "traffic_percent": 0,
        })
        if metrics is not None:
            state["latest_candidate_metrics"] = metrics
        state["events"].append({
            "type": "rollout_rolled_back",
            "reviewer": reviewer,
            "reason": reason,
            "created_at": datetime.now(UTC).isoformat(),
        })
        return self._save(state)

    def rollback(
        self,
        expected_revision: int,
        reviewer: str,
        reason: str,
        metrics: dict | None = None,
    ) -> dict:
        with self._locked():
            state = self.load()
            if state["revision"] != expected_revision:
                raise ValueError("rollout_revision_conflict")
            return self._rollback_state(state, reviewer, reason, metrics)

    def select(self, subject: str, default_model: str) -> str:
        state = self.load()
        if state.get("status") not in {"canary", "promoting", "active"}:
            return default_model
        bucket = int(hashlib.sha256(subject.encode()).hexdigest()[:8], 16) % 100
        return (
            state["candidate_model"]
            if bucket < state["traffic_percent"]
            else state["baseline_model"]
        )
