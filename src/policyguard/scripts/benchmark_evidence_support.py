import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from policyguard.application.embeddings import DenseRetriever
from policyguard.application.evidence_support import OpenAICompatibleEvidenceVerifier
from policyguard.application.onnx_embeddings import FastEmbedProvider
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def calculate_quality_metrics(cases: list[dict], labels: dict, decisions: list) -> dict:
    by_id = {item.case_id: item for item in decisions}
    positive_ids = [case_id for case_id, expected in labels.items() if expected]
    negative_ids = [case_id for case_id, expected in labels.items() if not expected]
    decided_positive = [case_id for case_id in positive_ids if case_id in by_id]
    decided_negative = [case_id for case_id in negative_ids if case_id in by_id]
    coverage = len(by_id) / len(cases) if cases else 0
    complete = len(by_id) == len(cases) and len(by_id) == len(decisions)
    return {
        "quality_metric_status": "valid" if complete else "invalid_incomplete_provider_run",
        "quality_metrics_valid": complete,
        "answerable_recall": (
            sum(by_id[case_id].supported for case_id in positive_ids) / len(positive_ids)
            if complete and positive_ids else None
        ),
        "no_answer_specificity": (
            sum(not by_id[case_id].supported for case_id in negative_ids) / len(negative_ids)
            if complete and negative_ids else None
        ),
        "observed_answerable_recall": (
            sum(by_id[case_id].supported for case_id in decided_positive)
            / len(decided_positive) if decided_positive else None
        ),
        "observed_no_answer_specificity": (
            sum(not by_id[case_id].supported for case_id in decided_negative)
            / len(decided_negative) if decided_negative else None
        ),
        "decision_coverage": coverage,
        "missing_decision_count": len(cases) - len(by_id),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positive-limit", type=int, default=10)
    parser.add_argument("--negative-limit", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=5)
    args = parser.parse_args()
    load_dotenv()
    root = Path(__file__).parents[3]
    positive = json.loads((root / "data/evaluation/rag-hard-v1.json").read_text(encoding="utf-8"))[
        "samples"
    ][: args.positive_limit]
    negative = json.loads(
        (root / "data/evaluation/rag-no-answer-near-v1.json").read_text(encoding="utf-8")
    )["samples"][: args.negative_limit]
    labeled = [(item, True) for item in positive] + [(item, False) for item in negative]
    database = Database("sqlite:///./data/policyguard.db")
    database.initialize()
    cases = []
    labels = {}
    with database.session_factory() as session:
        retriever = DenseRetriever(
            SqlAlchemyKnowledgeRepository(session),
            FastEmbedProvider("jinaai/jina-embeddings-v2-base-zh"),
        )
        for index, (sample, answerable) in enumerate(labeled):
            case_id = f"case-{index}"
            hits = retriever.search(
                sample["query"],
                top_k=3,
                scope=KnowledgeFilter(jurisdiction=sample["jurisdiction"]),
            )
            evidence = "\n\n".join(
                f"[{hit.chunk.section_id}] {hit.chunk.heading}\n{hit.chunk.text}" for hit in hits
            )
            cases.append({"case_id": case_id, "query": sample["query"], "evidence": evidence})
            labels[case_id] = answerable
    verifier = OpenAICompatibleEvidenceVerifier(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model=os.getenv("LLM_MODEL", "gpt-5.6-sol"),
        reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "medium"),
    )
    decisions = []
    usages = []
    latencies = []
    failures = []
    request_attempts = 0
    for start in range(0, len(cases), args.batch_size):
        batch = cases[start : start + args.batch_size]
        began = time.perf_counter()
        for attempt in range(2):
            request_attempts += 1
            try:
                batch_decisions, usage = verifier.verify_batch(batch)
                decisions.extend(batch_decisions)
                usages.append(usage)
                break
            except Exception as exc:
                if attempt == 1:
                    failures.append(f"{type(exc).__name__}: {exc}")
        latencies.append((time.perf_counter() - began) * 1000)
    by_id = {item.case_id: item for item in decisions}
    positive_count = sum(labels.values())
    negative_count = len(labels) - positive_count
    quality = calculate_quality_metrics(cases, labels, decisions)
    result = {
        "model": verifier.model,
        "reasoning_effort": verifier.reasoning_effort,
        "positive_count": positive_count,
        "negative_count": negative_count,
        **quality,
        "quote_valid_rate": sum(item.quote_valid for item in decisions) / len(decisions)
        if decisions
        else 0,
        "batch_count": len(latencies),
        "request_attempts": request_attempts,
        "mean_request_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
        "provider_total_tokens": sum(item.get("total_tokens", 0) for item in usages),
        "failures": failures,
        "cases": [
            {
                "case_id": case_id,
                "expected_supported": expected,
                "predicted_supported": (by_id[case_id].supported if case_id in by_id else None),
                "quote_valid": by_id[case_id].quote_valid if case_id in by_id else None,
            }
            for case_id, expected in labels.items()
        ],
    }
    safe_model = verifier.model.replace("/", "_").replace("\\", "_")
    output = root / f"data/benchmarks/evidence-support-{safe_model}-medium.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
