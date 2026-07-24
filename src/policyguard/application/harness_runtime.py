"""Event-sourced, revision-protected Agent Harness runtime."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from policyguard.application.harness_context import (
    ContextBudget,
    ContextItem,
    ContextManager,
    JsonContextCache,
    JsonMemoryStore,
)
from policyguard.application.security import detect_prompt_injection, redact_sensitive

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class HarnessRuntime:
    def __init__(self, upload_root: Path, tenant: str, tools: dict[str, ToolHandler]) -> None:
        tenant_key = sha256(tenant.encode("utf-8")).hexdigest()[:24]
        self.root = (upload_root / "harness" / tenant_key).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.tools = tools

    def create(
        self,
        *,
        objective: str,
        context: dict[str, Any],
        plan: list[dict[str, Any]],
        budgets: dict[str, int],
    ) -> dict[str, Any]:
        if not objective.strip():
            raise ValueError("harness_objective_required")
        if detect_prompt_injection({"objective": objective, "context": context}):
            raise ValueError("harness_prompt_injection_detected")
        if not plan or len(plan) > 24:
            raise ValueError("harness_plan_size_invalid")
        for item in plan:
            if item.get("type") not in {"tool", "finish"}:
                raise ValueError("harness_step_type_invalid")
            if item.get("type") == "tool" and item.get("tool") not in self.tools:
                raise ValueError("harness_tool_not_found")
        run_id = uuid4().hex
        now = datetime.now(UTC).isoformat()
        manifest = {
            "run_id": run_id,
            "revision": 0,
            "status": "ready",
            "objective": objective,
            "context": redact_sensitive(context),
            "plan": plan,
            "step_index": 0,
            "budgets": {
                "max_steps": min(max(int(budgets.get("max_steps", 8)), 1), 24),
                "max_tool_calls": min(max(int(budgets.get("max_tool_calls", 6)), 1), 20),
                "max_tokens": min(max(int(budgets.get("max_tokens", 12_000)), 256), 128_000),
                "max_cost_microusd": min(
                    max(int(budgets.get("max_cost_microusd", 100_000)), 0), 10_000_000
                ),
            },
            "usage": {"steps": 0, "tool_calls": 0, "tokens": 0, "cost_microusd": 0},
            "permissions": [],
            "pending_permission": None,
            "result": {},
            "context_metrics": {},
            "created_at": now,
            "updated_at": now,
        }
        directory = self._directory(run_id)
        directory.mkdir()
        self._save(directory, manifest)
        self._append_event(directory, manifest, "run.created", {"objective": objective})
        return self.load(run_id)

    def load(self, run_id: str) -> dict[str, Any]:
        directory = self._directory(run_id)
        path = directory / "manifest.json"
        if not path.is_file():
            raise LookupError("harness_run_not_found")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["events"] = self.events(run_id)
        return manifest

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = []
        for path in self.root.glob("*/manifest.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            manifest.pop("context", None)
            rows.append(manifest)
        return sorted(rows, key=lambda item: item["updated_at"], reverse=True)[:limit]

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        path = self._directory(run_id) / "events.jsonl"
        if not path.is_file():
            return []
        return [
            item
            for item in (
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            )
            if item["sequence"] > after
        ]

    def advance(self, run_id: str, expected_revision: int) -> dict[str, Any]:
        directory = self._directory(run_id)
        with self._lock(directory):
            manifest = self._manifest(directory, expected_revision)
            if manifest["status"] in TERMINAL_STATUSES:
                raise RuntimeError("harness_run_terminal")
            if manifest["status"] == "paused":
                raise RuntimeError("harness_run_paused")
            if manifest["usage"]["steps"] >= manifest["budgets"]["max_steps"]:
                return self._fail(directory, manifest, "step_budget_exceeded")
            step = manifest["plan"][manifest["step_index"]]
            manifest["status"] = "running"
            self._append_event(directory, manifest, "step.started", {
                "step_index": manifest["step_index"], "step": step,
            })
            required = step.get("permission")
            if required and required not in manifest["permissions"]:
                manifest["status"] = "paused"
                manifest["pending_permission"] = required
                self._append_event(directory, manifest, "permission.required", {
                    "permission": required, "tool": step.get("tool"),
                })
                self._checkpoint(directory, manifest, "permission_required")
                return self._commit(directory, manifest)
            if step["type"] == "finish":
                manifest["result"] = step.get("result", {"objective": manifest["objective"]})
                manifest["status"] = "completed"
                manifest["step_index"] += 1
                manifest["usage"]["steps"] += 1
                self._append_event(directory, manifest, "run.completed", manifest["result"])
                self._checkpoint(directory, manifest, "completed")
                return self._commit(directory, manifest)
            if manifest["usage"]["tool_calls"] >= manifest["budgets"]["max_tool_calls"]:
                return self._fail(directory, manifest, "tool_budget_exceeded")
            assembled = self._assemble_context(directory, manifest)
            manifest["context_metrics"] = assembled["metrics"]
            if assembled["metrics"]["used_tokens"] > manifest["budgets"]["max_tokens"]:
                return self._fail(directory, manifest, "token_budget_exceeded")
            started = perf_counter()
            try:
                output = self.tools[step["tool"]](step.get("arguments", {}), {
                    "run": manifest, "context": assembled,
                })
            except Exception as exc:
                self._append_event(directory, manifest, "tool.failed", {
                    "tool": step["tool"], "error": type(exc).__name__, "message": str(exc)[:500],
                })
                manifest["status"] = "paused"
                manifest["result"] = {"reason": "tool_failed", "retryable": True}
                self._checkpoint(directory, manifest, "tool_failed")
                return self._commit(directory, manifest)
            latency_ms = round((perf_counter() - started) * 1000, 2)
            max_tokens = manifest["budgets"]["max_tokens"]
            output_tokens = ContextManager(ContextBudget(
                max_tokens=max_tokens,
                reserved_output_tokens=min(2_000, max(64, max_tokens // 4)),
            )).assemble([
                ContextItem("tool-output", "tool_result", output, priority=90)
            ])["metrics"]["used_tokens"]
            manifest["usage"]["steps"] += 1
            manifest["usage"]["tool_calls"] += 1
            manifest["usage"]["tokens"] += assembled["metrics"]["used_tokens"] + output_tokens
            manifest["context"]["last_observation"] = redact_sensitive(output)
            manifest["step_index"] += 1
            self._append_event(directory, manifest, "tool.completed", {
                "tool": step["tool"], "latency_ms": latency_ms,
                "output": redact_sensitive(output), "output_tokens": output_tokens,
            })
            self._checkpoint(directory, manifest, "step_completed")
            if manifest["step_index"] >= len(manifest["plan"]):
                manifest["status"] = "completed"
                manifest["result"] = {"last_observation": output}
                self._append_event(directory, manifest, "run.completed", manifest["result"])
            else:
                manifest["status"] = "ready"
            return self._commit(directory, manifest)

    def run_until_boundary(self, run_id: str, expected_revision: int) -> dict[str, Any]:
        current = self.load(run_id)
        if current["revision"] != expected_revision:
            raise RuntimeError("harness_revision_conflict")
        while current["status"] not in TERMINAL_STATUSES | {"paused"}:
            current = self.advance(run_id, current["revision"])
        return current

    def pause(self, run_id: str, expected_revision: int, reason: str) -> dict[str, Any]:
        return self._transition(
            run_id, expected_revision, "paused", "run.paused", {"reason": reason}
        )

    def resume(self, run_id: str, expected_revision: int) -> dict[str, Any]:
        directory = self._directory(run_id)
        with self._lock(directory):
            manifest = self._manifest(directory, expected_revision)
            if manifest["status"] != "paused" or manifest["pending_permission"]:
                raise RuntimeError("harness_run_not_resumable")
            manifest["status"] = "ready"
            self._append_event(directory, manifest, "run.resumed", {})
            return self._commit(directory, manifest)

    def approve_permission(
        self, run_id: str, expected_revision: int, permission: str, reviewer: str
    ) -> dict[str, Any]:
        directory = self._directory(run_id)
        with self._lock(directory):
            manifest = self._manifest(directory, expected_revision)
            if manifest.get("pending_permission") != permission:
                raise RuntimeError("harness_permission_not_pending")
            manifest["permissions"] = sorted(set(manifest["permissions"] + [permission]))
            manifest["pending_permission"] = None
            manifest["status"] = "ready"
            self._append_event(directory, manifest, "permission.approved", {
                "permission": permission, "reviewer": reviewer,
            })
            return self._commit(directory, manifest)

    def put_memory(
        self,
        run_id: str,
        expected_revision: int,
        tier: str,
        key: str,
        value: Any,
        reviewed: bool,
    ) -> dict[str, Any]:
        directory = self._directory(run_id)
        with self._lock(directory):
            manifest = self._manifest(directory, expected_revision)
            if manifest["status"] in TERMINAL_STATUSES:
                raise RuntimeError("harness_run_terminal")
            JsonMemoryStore(directory / "memory.json").put(
                tier, key, redact_sensitive(value), reviewed=reviewed
            )
            self._append_event(directory, manifest, "memory.updated", {
                "tier": tier, "key": key, "reviewed": reviewed,
            })
            self._checkpoint(directory, manifest, "memory_updated")
            return self._commit(directory, manifest)

    def cancel(self, run_id: str, expected_revision: int) -> dict[str, Any]:
        return self._transition(run_id, expected_revision, "cancelled", "run.cancelled", {})

    def _transition(
        self, run_id: str, expected_revision: int, status: str, event: str, detail: dict
    ) -> dict[str, Any]:
        directory = self._directory(run_id)
        with self._lock(directory):
            manifest = self._manifest(directory, expected_revision)
            if manifest["status"] in TERMINAL_STATUSES:
                raise RuntimeError("harness_run_terminal")
            manifest["status"] = status
            self._append_event(directory, manifest, event, detail)
            self._checkpoint(directory, manifest, event)
            return self._commit(directory, manifest)

    def _assemble_context(self, directory: Path, manifest: dict) -> dict[str, Any]:
        items = [
            ContextItem("objective", "instruction", manifest["objective"], 100, True),
            ContextItem("input", "user_input", manifest["context"], 90, True),
        ]
        memory = JsonMemoryStore(directory / "memory.json")
        for tier in ("working", "episodic", "long_term"):
            for key, value in memory.items(tier).items():
                items.append(ContextItem(
                    f"memory:{tier}:{key}", f"memory_{tier}", value["value"],
                    80 if tier == "working" else 60 if tier == "episodic" else 50,
                ))
        max_tokens = manifest["budgets"]["max_tokens"]
        manager = ContextManager(ContextBudget(
            max_tokens=max_tokens,
            reserved_output_tokens=min(2_000, max(64, max_tokens // 4)),
        ))
        assembled = manager.assemble(items)
        cache = JsonContextCache(directory / "context-cache.json")
        digest, hit = cache.get_or_put(assembled["items"])
        assembled["metrics"].update({
            "prefix_hash": digest,
            "prefix_cache_hit": hit,
            "cache": cache.metrics(),
        })
        return assembled

    def _fail(self, directory: Path, manifest: dict, reason: str) -> dict[str, Any]:
        manifest["status"] = "failed"
        manifest["result"] = {"reason": reason}
        self._append_event(directory, manifest, "run.failed", {"reason": reason})
        self._checkpoint(directory, manifest, reason)
        return self._commit(directory, manifest)

    def _manifest(self, directory: Path, expected_revision: int) -> dict[str, Any]:
        path = directory / "manifest.json"
        if not path.is_file():
            raise LookupError("harness_run_not_found")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["revision"] != expected_revision:
            raise RuntimeError("harness_revision_conflict")
        return manifest

    def _commit(self, directory: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        manifest["revision"] += 1
        manifest["updated_at"] = datetime.now(UTC).isoformat()
        self._save(directory, manifest)
        return self.load(manifest["run_id"])

    def _append_event(
        self, directory: Path, manifest: dict[str, Any], event_type: str, detail: dict[str, Any]
    ) -> None:
        existing = self.events(manifest["run_id"])
        event = {
            "sequence": len(existing) + 1,
            "type": event_type,
            "status": manifest["status"],
            "detail": detail,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _checkpoint(self, directory: Path, manifest: dict[str, Any], reason: str) -> None:
        checkpoint = {
            "reason": reason,
            "revision": manifest["revision"],
            "status": manifest["status"],
            "step_index": manifest["step_index"],
            "usage": manifest["usage"],
            "context_sha256": sha256(
                json.dumps(manifest["context"], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
            "created_at": datetime.now(UTC).isoformat(),
        }
        sequence = len(list(directory.glob("checkpoint-*.json"))) + 1
        path = directory / f"checkpoint-{sequence:04d}.json"
        path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_event(directory, manifest, "checkpoint.saved", checkpoint)

    def _directory(self, run_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise LookupError("harness_run_not_found")
        directory = (self.root / run_id).resolve()
        if directory.parent != self.root:
            raise LookupError("harness_run_not_found")
        return directory

    @contextmanager
    def _lock(self, directory: Path):
        lock = directory / ".lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError("harness_run_locked") from exc
        try:
            os.close(descriptor)
            yield
        finally:
            lock.unlink(missing_ok=True)

    @staticmethod
    def _save(directory: Path, manifest: dict[str, Any]) -> None:
        path = directory / "manifest.json"
        temporary = directory / "manifest.tmp"
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
