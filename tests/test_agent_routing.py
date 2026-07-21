import json
from pathlib import Path

from policyguard.application.agent_routing import deterministic_route, evaluate_router


def test_dynamic_router_handles_parser_retrieval_and_human_routes() -> None:
    root = Path(__file__).parents[1]
    cases = json.loads(
        (root / "data/evaluation/agent-routing-v1.json").read_text(encoding="utf-8")
    )["samples"]
    report = evaluate_router(cases, deterministic_route)
    assert report["metrics"]["case_count"] == 10
    assert report["metrics"]["route_accuracy"] == 1.0
    assert report["metrics"]["manual_review_recall"] == 1.0
