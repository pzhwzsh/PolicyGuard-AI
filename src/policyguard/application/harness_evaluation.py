"""Agent Harness evaluation for task, tool, recovery, latency, and budget behavior."""

from __future__ import annotations

from collections.abc import Callable
from math import ceil
from statistics import mean
from time import perf_counter
from typing import Any


def _nearest_rank_percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def evaluate_harness(
    cases: list[dict[str, Any]], runner: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    rows = []
    for case in cases:
        started = perf_counter()
        try:
            result = runner(case)
            status = result.get("status", "unknown")
            tools = [
                event["detail"].get("tool")
                for event in result.get("events", [])
                if event["type"] == "tool.completed"
            ]
            expected = case.get("expected_tools", [])
            correct = sum(tool in expected for tool in tools)
            precision = correct / len(tools) if tools else float(not expected)
            recall = correct / len(expected) if expected else 1.0
            success = status == case.get("expected_status", "completed")
            recovery = any(
                event["type"] in {"run.resumed", "checkpoint.saved"}
                for event in result.get("events", [])
            )
            error = None
        except Exception as exc:
            result, status, tools = {}, "exception", []
            precision = recall = 0.0
            success = recovery = False
            error = type(exc).__name__
        rows.append({
            "case_id": case["case_id"],
            "success": success,
            "status": status,
            "tool_precision": round(precision, 4),
            "tool_recall": round(recall, 4),
            "recovery_signal": recovery,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "tokens": result.get("usage", {}).get("tokens", 0),
            "cost_microusd": result.get("usage", {}).get("cost_microusd", 0),
            "error": error,
        })
    count = max(len(rows), 1)
    latencies = [row["latency_ms"] for row in rows]
    return {
        "metrics": {
            "case_count": len(rows),
            "task_success_rate": round(sum(row["success"] for row in rows) / count, 4),
            "mean_tool_precision": round(mean(row["tool_precision"] for row in rows), 4)
            if rows else 0.0,
            "mean_tool_recall": round(mean(row["tool_recall"] for row in rows), 4)
            if rows else 0.0,
            "recovery_signal_rate": round(
                sum(row["recovery_signal"] for row in rows) / count, 4
            ),
            "mean_latency_ms": round(mean(row["latency_ms"] for row in rows), 2)
            if rows else 0.0,
            "p95_latency_ms": _nearest_rank_percentile(latencies, 0.95),
            "total_tokens": sum(row["tokens"] for row in rows),
            "total_cost_microusd": sum(row["cost_microusd"] for row in rows),
        },
        "cases": rows,
    }
