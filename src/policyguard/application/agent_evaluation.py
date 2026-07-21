"""Comparable Agent and deterministic-pipeline remediation evaluation."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from time import perf_counter

from policyguard.application.agent import ControlledAgent


@dataclass(frozen=True, slots=True)
class ExecutionMetrics:
    case_count: int
    success_rate: float
    manual_review_rate: float
    mean_latency_ms: float
    mean_tool_calls: float
    mean_planner_calls: float
    total_tokens: int


def _passes(result: dict, expected_removed: list[str]) -> bool:
    if result.get("external_side_effect") is not False:
        return False
    operations = result.get("operations", [])
    if expected_removed and not operations:
        return False
    rewritten = " ".join(str(operation.get("after", "")) for operation in operations)
    removed = {
        phrase for operation in operations for phrase in operation.get("removed_phrases", [])
    }
    return all(phrase not in rewritten and phrase in removed for phrase in expected_removed)


def evaluate_agent(cases: list[dict], agent_factory: Callable[[], ControlledAgent]) -> dict:
    rows = []
    for case in cases:
        started = perf_counter()
        outcome = agent_factory().run({"product": case["product"]})
        rows.append({
            "case_id": case["case_id"],
            "success": outcome.status == "completed"
            and _passes(outcome.result, case.get("expected_removed", [])),
            "manual_review": outcome.status == "manual_review",
            "latency_ms": (perf_counter() - started) * 1000,
            "tool_calls": outcome.tool_calls,
            "planner_calls": outcome.planner_calls,
            "total_tokens": outcome.total_tokens,
        })
    count = max(len(rows), 1)
    metrics = ExecutionMetrics(
        case_count=len(rows),
        success_rate=round(sum(row["success"] for row in rows) / count, 4),
        manual_review_rate=round(sum(row["manual_review"] for row in rows) / count, 4),
        mean_latency_ms=round(sum(row["latency_ms"] for row in rows) / count, 2),
        mean_tool_calls=round(sum(row["tool_calls"] for row in rows) / count, 2),
        mean_planner_calls=round(sum(row["planner_calls"] for row in rows) / count, 2),
        total_tokens=sum(row["total_tokens"] for row in rows),
    )
    return {"metrics": asdict(metrics), "cases": rows}


def evaluate_pipeline(cases: list[dict], pipeline: Callable[[dict], dict]) -> dict:
    rows = []
    for case in cases:
        started = perf_counter()
        try:
            result = pipeline(case["product"])
            success = _passes(result, case.get("expected_removed", []))
            manual_review = False
        except Exception:
            success = False
            manual_review = True
        rows.append({
            "case_id": case["case_id"], "success": success,
            "manual_review": manual_review,
            "latency_ms": (perf_counter() - started) * 1000,
            "tool_calls": 1, "planner_calls": 0, "total_tokens": 0,
        })
    count = max(len(rows), 1)
    metrics = ExecutionMetrics(
        case_count=len(rows),
        success_rate=round(sum(row["success"] for row in rows) / count, 4),
        manual_review_rate=round(sum(row["manual_review"] for row in rows) / count, 4),
        mean_latency_ms=round(sum(row["latency_ms"] for row in rows) / count, 2),
        mean_tool_calls=1.0 if rows else 0.0,
        mean_planner_calls=0.0,
        total_tokens=0,
    )
    return {"metrics": asdict(metrics), "cases": rows}
