"""Auditable asynchronous retrieval-model comparisons."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from policyguard.application.embeddings import DenseRetriever
from policyguard.application.knowledge import BM25Retriever, evaluate_retriever
from policyguard.application.local_embeddings import LocalSentenceTransformerProvider
from policyguard.application.onnx_embeddings import FastEmbedProvider
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def dataset_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_evaluation_path(root: Path, requested: str) -> Path:
    evaluation_root = (root / "data" / "evaluation").resolve()
    path = (root / requested).resolve()
    if evaluation_root not in path.parents or path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError("evaluation_dataset_invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("samples"), list) or not payload["samples"]:
        raise ValueError("evaluation_dataset_empty")
    return path


def run_retrieval_evaluation(
    session,
    *,
    root: Path,
    dataset: str,
    candidates: list[str],
    top_k: int,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    dataset_path = validate_evaluation_path(root, dataset)
    repository = SqlAlchemyKnowledgeRepository(session)
    rows = []
    for candidate in candidates:
        started = time.perf_counter()
        try:
            if candidate == "bm25":
                retriever = BM25Retriever(repository)
                provider = "local_lexical"
            else:
                embedding_provider = (
                    LocalSentenceTransformerProvider(candidate)
                    if candidate == "BAAI/bge-m3"
                    else FastEmbedProvider(candidate)
                )
                retriever = DenseRetriever(repository, embedding_provider)
                provider = embedding_provider.provider_name
            metrics = evaluate_retriever(retriever, dataset_path, top_k=top_k)
            rows.append({
                "model": candidate,
                "provider": provider,
                "status": "ok",
                "sample_count": metrics.sample_count,
                "hit_rate_at_k": round(metrics.hit_rate_at_k, 4),
                "mean_reciprocal_rank": round(metrics.mean_reciprocal_rank, 4),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "estimated_cost_usd": 0.0,
                "error": None,
            })
        except Exception as exc:
            rows.append({
                "model": candidate,
                "provider": (
                    "sentence_transformers_local"
                    if candidate == "BAAI/bge-m3"
                    else "fastembed_onnx_local"
                ),
                "status": "failed",
                "sample_count": 0,
                "hit_rate_at_k": 0.0,
                "mean_reciprocal_rank": 0.0,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "estimated_cost_usd": 0.0,
                "error": f"{type(exc).__name__}: {exc}",
            })
    eligible = [
        row for row in rows
        if row["status"] == "ok"
        and row["hit_rate_at_k"] >= thresholds["min_hit_rate_at_k"]
        and row["mean_reciprocal_rank"] >= thresholds["min_mrr"]
        and row["latency_ms"] <= thresholds["max_latency_ms"]
    ]
    recommendation = max(
        eligible,
        key=lambda row: (row["mean_reciprocal_rank"], row["hit_rate_at_k"], -row["latency_ms"]),
        default=None,
    )
    return {
        "dataset": str(dataset_path.relative_to(root)).replace("\\", "/"),
        "dataset_sha256": dataset_fingerprint(dataset_path),
        "top_k": top_k,
        "thresholds": thresholds,
        "results": rows,
        "recommended_model": recommendation["model"] if recommendation else None,
        "promotion_status": "awaiting_human_approval" if recommendation else "thresholds_not_met",
        "measured_at": datetime.now(UTC).isoformat(),
    }
