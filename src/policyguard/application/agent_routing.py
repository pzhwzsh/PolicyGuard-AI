"""Deterministic baseline and evaluation contract for dynamic tool routing."""

from dataclasses import asdict, dataclass
from time import perf_counter

ROUTES = {
    "native_pdf": "parse_native_pdf",
    "scanned_pdf": "parse_with_ocr",
    "complex_table": "parse_with_structure",
    "language_mismatch": "rewrite_query",
    "weak_retrieval": "rewrite_query",
    "unsupported_evidence": "request_human_review",
    "prompt_injection": "request_human_review",
}


def deterministic_route(context: dict) -> str:
    if context.get("prompt_injection") or context.get("evidence_supported") is False:
        return "request_human_review"
    if context.get("scan_requires_ocr"):
        return "parse_with_ocr"
    if context.get("complex_tables"):
        return "parse_with_structure"
    if context.get("document_type") == "native_pdf":
        return "parse_native_pdf"
    if context.get("language_mismatch") or context.get("top_score", 1.0) < 0.45:
        return "rewrite_query"
    return "request_human_review"


@dataclass(frozen=True, slots=True)
class RoutingMetrics:
    case_count: int
    route_accuracy: float
    manual_review_recall: float
    mean_latency_ms: float


def evaluate_router(cases: list[dict], router) -> dict:
    rows = []
    for case in cases:
        started = perf_counter()
        route = router(case["context"])
        rows.append({
            "case_id": case["case_id"],
            "expected_route": case["expected_route"],
            "actual_route": route,
            "correct": route == case["expected_route"],
            "latency_ms": (perf_counter() - started) * 1000,
        })
    manual = [row for row in rows if row["expected_route"] == "request_human_review"]
    metrics = RoutingMetrics(
        case_count=len(rows),
        route_accuracy=round(sum(row["correct"] for row in rows) / max(len(rows), 1), 4),
        manual_review_recall=round(
            sum(row["actual_route"] == "request_human_review" for row in manual)
            / max(len(manual), 1),
            4,
        ),
        mean_latency_ms=round(
            sum(row["latency_ms"] for row in rows) / max(len(rows), 1), 3
        ),
    )
    return {"metrics": asdict(metrics), "cases": rows}
