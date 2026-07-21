"""Reproducible model benchmarks and token/cost accounting.

The module deliberately keeps provider calls behind the existing ports. A benchmark
with no configured provider is a skipped run, never a fabricated score.
"""

from __future__ import annotations

import csv
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Protocol

from policyguard.application.embeddings import DenseRetriever, EmbeddingProvider
from policyguard.domain.models import KnowledgeFilter


class Searcher(Protocol):
    def search(self, query: str, top_k: int, scope: KnowledgeFilter | None = None): ...


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    input_tokens: int
    output_tokens: int = 0
    method: str = "heuristic"


def estimate_tokens(text: str) -> int:
    """Conservative offline estimate; provider usage should replace it when available."""
    cjk = sum("\u4e00" <= char <= "\u9fff" for char in text)
    non_cjk = text.translate({ord(char): " " for char in text if "\u4e00" <= char <= "\u9fff"})
    words = len(non_cjk.split())
    punctuation = max(0, len(text) - cjk - sum(char.isspace() for char in non_cjk))
    return max(1, cjk + words + punctuation // 4)


def compress_context(hits: list[Any], max_chars: int = 6000, max_chunks: int = 5) -> list[Any]:
    """Keep ranked, non-duplicate evidence while preserving citation-bearing chunks."""
    selected: list[Any] = []
    seen: set[str] = set()
    used = 0
    for hit in hits:
        chunk = hit.chunk
        key = " ".join(chunk.text.casefold().split())
        if key in seen:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        text = chunk.text if len(chunk.text) <= remaining else chunk.text[:remaining]
        if text != chunk.text:
            from dataclasses import is_dataclass, replace

            if is_dataclass(chunk) and is_dataclass(hit):
                hit = replace(hit, chunk=replace(chunk, text=text))
            else:
                # Test doubles and adapter objects may not be dataclasses.
                import copy

                cloned_chunk = copy.copy(chunk)
                cloned_hit = copy.copy(hit)
                cloned_chunk.text = text
                cloned_hit.chunk = cloned_chunk
                hit = cloned_hit
        selected.append(hit)
        seen.add(key)
        used += len(text)
        if len(selected) >= max_chunks:
            break
    return selected


def estimate_cost(
    estimate: TokenEstimate,
    *,
    input_price_per_million: float = 0.0,
    output_price_per_million: float = 0.0,
) -> float:
    return (
        estimate.input_tokens * input_price_per_million
        + estimate.output_tokens * output_price_per_million
    ) / 1_000_000


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    component: str
    provider: str
    model: str
    dataset: str
    sample_count: int
    hit_rate_at_k: float
    mean_reciprocal_rank: float
    mean_latency_ms: float
    p95_latency_ms: float
    estimated_input_tokens: int
    cache_hit_rate: float | None
    failures: int
    status: str
    error: str | None = None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, int(round((len(values) - 1) * percentile)))
    return values[index]


def run_embedding_benchmark(
    repository: Any,
    provider_factory: Callable[[dict[str, Any]], EmbeddingProvider],
    matrix_path: Path,
    dataset_path: Path,
    top_k: int = 5,
) -> list[BenchmarkResult]:
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    results: list[BenchmarkResult] = []
    for candidate in matrix.get("embedding", []):
        model = candidate["model"]
        started = time.perf_counter()
        latencies: list[float] = []
        hits = 0
        rr_sum = 0.0
        failures = 0
        estimated_tokens = 0
        try:
            provider = provider_factory(candidate)
            retriever = DenseRetriever(repository, provider)
            for sample in dataset["samples"]:
                scope = KnowledgeFilter(
                    jurisdiction=sample["jurisdiction"],
                    category=sample.get("category", "all"),
                    channel=sample.get("channel", "all"),
                    as_of=sample.get("as_of"),
                )
                estimated_tokens += estimate_tokens(sample["query"])
                query_started = time.perf_counter()
                found = retriever.search(sample["query"], top_k=top_k, scope=scope)
                latencies.append((time.perf_counter() - query_started) * 1000)
                ranks = [
                    hit.rank
                    for hit in found
                    if hit.chunk.section_id == sample["expected_section_id"]
                ]
                if ranks:
                    hits += 1
                    rr_sum += 1 / ranks[0]
            count = len(dataset["samples"])
            results.append(
                BenchmarkResult(
                    "embedding",
                    provider.provider_name,
                    model,
                    str(dataset_path),
                    count,
                    hits / count if count else 0.0,
                    rr_sum / count if count else 0.0,
                    mean(latencies) if latencies else 0.0,
                    _percentile(latencies, 0.95),
                    estimated_tokens,
                    None,
                    failures,
                    "ok",
                )
            )
        except Exception as exc:
            results.append(
                BenchmarkResult(
                    "embedding",
                    candidate.get("provider", "unknown"),
                    model,
                    str(dataset_path),
                    0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    estimated_tokens,
                    None,
                    failures + 1,
                    "failed",
                    type(exc).__name__ + ": " + str(exc),
                )
            )
        _ = started
    return results


def write_results(results: list[BenchmarkResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    csv_path = output_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(asdict(results[0]).keys()) if results else ["status"]
        )
        writer.writeheader()
        writer.writerows(asdict(item) for item in results)
