import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from policyguard.application.embeddings import DenseRetriever
from policyguard.application.onnx_embeddings import FastEmbedProvider
from policyguard.application.query_rewrite import (
    JsonQueryRewriteCache,
    OpenAICompatibleQueryRewriter,
    cached_rewrite_batch,
    multi_query_search,
    should_rewrite,
)
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def metrics(rows: list[tuple[str, list]]) -> dict:
    hits = 0
    reciprocal = 0.0
    for expected, results in rows:
        ranks = [item.rank for item in results if item.chunk.section_id == expected]
        if ranks:
            hits += 1
            reciprocal += 1 / ranks[0]
    count = len(rows)
    return {
        "sample_count": count,
        "hit_rate_at_5": hits / count if count else 0,
        "mrr": reciprocal / count if count else 0,
    }


def main() -> None:
    load_dotenv()
    root = Path(__file__).parents[3]
    samples = json.loads((root / "data/evaluation/rag-hard-v1.json").read_text(encoding="utf-8"))[
        "samples"
    ]
    rewriter = OpenAICompatibleQueryRewriter(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model=os.getenv("LLM_MODEL", "gpt-5.6-sol"),
        reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "medium"),
    )
    cache = JsonQueryRewriteCache(root / "data/query-cache/rewrites.json")
    rewrites = []
    total_tokens = 0
    request_count = 0
    started = time.perf_counter()
    for offset in range(0, len(samples), 10):
        items = [
            {
                "query_id": f"q-{offset + index}",
                "query": sample["query"],
                "jurisdiction": sample["jurisdiction"],
            }
            for index, sample in enumerate(samples[offset : offset + 10])
        ]
        batch, usage = cached_rewrite_batch(rewriter, cache, items)
        rewrites.extend(batch)
        if usage:
            request_count += 1
            total_tokens += usage.get("total_tokens", 0)
    rewrite_latency = (time.perf_counter() - started) * 1000
    database = Database("sqlite:///./data/policyguard.db")
    database.initialize()
    original_rows = []
    canonical_rows = []
    fused_rows = []
    selective_rows = []
    selectively_rewritten = 0
    with database.session_factory() as session:
        dense = DenseRetriever(
            SqlAlchemyKnowledgeRepository(session),
            FastEmbedProvider("jinaai/jina-embeddings-v2-base-zh"),
        )
        for sample, rewrite in zip(samples, rewrites, strict=True):
            scope = KnowledgeFilter(jurisdiction=sample["jurisdiction"])
            expected = sample["expected_section_id"]
            original = dense.search(sample["query"], 5, scope)
            original_rows.append((expected, original))
            canonical_rows.append((expected, dense.search(rewrite.canonical_query, 5, scope)))
            fused_rows.append((expected, multi_query_search(dense, rewrite, 5, scope)))
            top_score = original[0].score if original else None
            if should_rewrite(sample["query"], sample["jurisdiction"], top_score):
                selective_rows.append((expected, multi_query_search(dense, rewrite, 5, scope)))
                selectively_rewritten += 1
            else:
                selective_rows.append((expected, original))
    result = {
        "model": rewriter.model,
        "embedding": "jinaai/jina-embeddings-v2-base-zh",
        "rewrite_request_count": request_count,
        "rewrite_total_tokens": total_tokens,
        "rewrite_wall_time_ms": rewrite_latency,
        "original": metrics(original_rows),
        "canonical_only": metrics(canonical_rows),
        "multi_query_rrf": metrics(fused_rows),
        "selective_multi_query_rrf": {
            **metrics(selective_rows),
            "rewritten_count": selectively_rewritten,
        },
        "examples": [
            {
                "original": item.original_query,
                "canonical": item.canonical_query,
                "alternatives": list(item.alternatives),
                "legal_terms": list(item.legal_terms),
            }
            for item in rewrites[:5]
        ],
    }
    output = root / "data/benchmarks/query-rewrite-hard-v1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
