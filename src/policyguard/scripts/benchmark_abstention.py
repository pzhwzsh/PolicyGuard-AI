"""Calibrate a development-only no-answer threshold for one embedding model."""

import json
from pathlib import Path

from policyguard.application.embeddings import DenseRetriever
from policyguard.application.onnx_embeddings import FastEmbedProvider
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository

MODEL = "jinaai/jina-embeddings-v2-base-zh"
HELDOUT_THRESHOLD = 0.311966


def _top_score(retriever, sample: dict) -> float:
    hits = retriever.search(
        sample["query"],
        top_k=1,
        scope=KnowledgeFilter(jurisdiction=sample["jurisdiction"]),
    )
    return hits[0].score if hits else 0.0


def _best_threshold(positive: list[float], negative: list[float]) -> dict:
    candidates = sorted(set(positive + negative))
    thresholds = (
        [0.0]
        + [(left + right) / 2 for left, right in zip(candidates, candidates[1:], strict=False)]
        + [1.0]
    )
    scored = []
    for threshold in thresholds:
        true_positive = sum(score >= threshold for score in positive)
        true_negative = sum(score < threshold for score in negative)
        recall = true_positive / len(positive) if positive else 0.0
        specificity = true_negative / len(negative) if negative else 0.0
        scored.append((recall + specificity, recall, specificity, threshold))
    _, recall, specificity, threshold = max(scored)
    return {
        "threshold": round(threshold, 6),
        "answerable_recall": round(recall, 6),
        "no_answer_specificity": round(specificity, 6),
        "balanced_accuracy": round((recall + specificity) / 2, 6),
    }


def main() -> None:
    root = Path(__file__).parents[3]
    positives = json.loads((root / "data/evaluation/rag-hard-v1.json").read_text(encoding="utf-8"))[
        "samples"
    ]
    negatives = json.loads(
        (root / "data/evaluation/rag-no-answer-v1.json").read_text(encoding="utf-8")
    )["samples"]
    heldout = json.loads(
        (root / "data/evaluation/rag-no-answer-near-v1.json").read_text(encoding="utf-8")
    )["samples"]
    database = Database("sqlite:///./data/policyguard.db")
    database.initialize()
    with database.session_factory() as session:
        retriever = DenseRetriever(SqlAlchemyKnowledgeRepository(session), FastEmbedProvider(MODEL))
        positive_scores = [_top_score(retriever, sample) for sample in positives]
        negative_scores = [_top_score(retriever, sample) for sample in negatives]
        heldout_scores = [_top_score(retriever, sample) for sample in heldout]
    heldout_specificity = sum(score < HELDOUT_THRESHOLD for score in heldout_scores) / len(
        heldout_scores
    )
    result = {
        "model": MODEL,
        "positive_count": len(positive_scores),
        "negative_count": len(negative_scores),
        "positive_score_range": [min(positive_scores), max(positive_scores)],
        "negative_score_range": [min(negative_scores), max(negative_scores)],
        "near_domain_heldout_count": len(heldout_scores),
        "near_domain_score_range": [min(heldout_scores), max(heldout_scores)],
        "heldout_threshold": HELDOUT_THRESHOLD,
        "near_domain_specificity": round(heldout_specificity, 6),
        **_best_threshold(positive_scores, negative_scores),
        "warning": "Development calibration only; validate on a held-out set before runtime use.",
    }
    output = root / "data/benchmarks/abstention-jina-hard-v1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
