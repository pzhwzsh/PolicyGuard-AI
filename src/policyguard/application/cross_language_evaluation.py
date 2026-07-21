from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
import json

from policyguard.application.benchmark import estimate_tokens
from policyguard.domain.models import KnowledgeFilter


@dataclass(frozen=True, slots=True)
class RetrievalMeasurement:
    name: str
    sample_count: int
    answerable_count: int
    hit_rate_at_5: float
    mrr: float
    mean_latency_ms: float
    p95_latency_ms: float
    estimated_query_tokens: int
    failures: int
    no_answer_count: int
    no_answer_candidate_presence_rate: float
    no_answer_mean_top_score: float


def load_cross_language_dataset(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = [item["id"] for item in payload["samples"]]
    if len(ids) != len(set(ids)):
        raise ValueError("cross_language_dataset_duplicate_id")
    if payload.get("label_status") not in {
        "ai_assisted_pending_human_review", "human_verified"
    }:
        raise ValueError("cross_language_dataset_invalid_label_status")
    return payload


def measure_retriever(name: str, retriever, samples: list[dict]) -> RetrievalMeasurement:
    hits = 0
    reciprocal = 0.0
    failures = 0
    latencies = []
    positives = [item for item in samples if item["answerable"]]
    negatives = [item for item in samples if not item["answerable"]]
    for sample in positives:
        started = perf_counter()
        try:
            results = retriever.search(
                sample["query"], 5,
                KnowledgeFilter(jurisdiction=sample["jurisdiction"]),
            )
        except Exception:
            failures += 1
            results = []
        latencies.append((perf_counter() - started) * 1000)
        ranks = [
            item.rank for item in results
            if item.chunk.section_id == sample["expected_section_id"]
        ]
        if ranks:
            hits += 1
            reciprocal += 1 / ranks[0]
    ordered = sorted(latencies)
    p95_index = min(len(ordered) - 1, round((len(ordered) - 1) * 0.95)) if ordered else 0
    count = len(positives)
    negative_scores = []
    for sample in negatives:
        try:
            results = retriever.search(
                sample["query"], 5,
                KnowledgeFilter(jurisdiction=sample["jurisdiction"]),
            )
        except Exception:
            failures += 1
            results = []
        if results:
            negative_scores.append(results[0].score)
    return RetrievalMeasurement(
        name=name,
        sample_count=len(samples),
        answerable_count=count,
        hit_rate_at_5=round(hits / count, 6) if count else 0.0,
        mrr=round(reciprocal / count, 6) if count else 0.0,
        mean_latency_ms=round(mean(latencies), 3) if latencies else 0.0,
        p95_latency_ms=round(ordered[p95_index], 3) if ordered else 0.0,
        estimated_query_tokens=sum(estimate_tokens(item["query"]) for item in positives),
        failures=failures,
        no_answer_count=len(negatives),
        no_answer_candidate_presence_rate=round(
            len(negative_scores) / len(negatives), 6
        ) if negatives else 0.0,
        no_answer_mean_top_score=round(mean(negative_scores), 6) if negative_scores else 0.0,
    )
