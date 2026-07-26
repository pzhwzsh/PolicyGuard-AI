import argparse
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from policyguard.application.cross_language_evaluation import (
    load_cross_language_dataset,
    measure_retriever,
)
from policyguard.application.embeddings import DenseRetriever, OpenAICompatibleEmbeddingProvider
from policyguard.application.hybrid import HybridRetriever
from policyguard.application.knowledge import BM25Retriever, ingest_source_directory
from policyguard.application.local_embeddings import LocalSentenceTransformerProvider
from policyguard.application.onnx_embeddings import FastEmbedProvider
from policyguard.application.query_rewrite import (
    JsonQueryRewriteCache,
    OpenAICompatibleQueryRewriter,
    cached_rewrite_batch,
    multi_query_search,
)
from policyguard.config import get_settings
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


class RewrittenRetriever:
    def __init__(self, retriever, rewrites: dict[tuple[str, str], object]) -> None:
        self.retriever = retriever
        self.rewrites = rewrites

    def search(self, query: str, top_k: int, scope: KnowledgeFilter):
        rewrite = self.rewrites.get((query, scope.jurisdiction))
        if rewrite is None:
            return self.retriever.search(query, top_k, scope)
        return multi_query_search(self.retriever, rewrite, top_k, scope)


class CachedEmbeddingProvider:
    """Batch-prewarmed provider so benchmark modes reuse identical query vectors."""

    def __init__(self, provider) -> None:
        self.provider = provider
        self.cache: dict[str, list[float]] = {}

    @property
    def provider_name(self) -> str:
        return self.provider.provider_name

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        missing = list(dict.fromkeys(text for text in texts if text not in self.cache))
        if missing:
            vectors = self.provider.embed(missing)
            if len(vectors) != len(missing):
                raise ValueError("embedding_provider_length_mismatch")
            self.cache.update(dict(zip(missing, vectors, strict=True)))
        return [self.cache[text] for text in texts]


def _rewrite_queries(settings, samples: list[dict], cache_path: Path) -> tuple[dict, dict]:
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return {}, {"status": "skipped", "reason": "llm_not_configured"}
    rewriter = OpenAICompatibleQueryRewriter(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    items = [
        {"query_id": item["id"], "query": item["query"],
         "jurisdiction": item["jurisdiction"]}
        for item in samples if item["answerable"]
    ]
    started = perf_counter()
    rewrites = []
    usage = {"total_tokens": 0, "cache_hits": 0, "cache_misses": 0}
    for offset in range(0, len(items), 10):
        batch, batch_usage = cached_rewrite_batch(
            rewriter, JsonQueryRewriteCache(cache_path), items[offset: offset + 10]
        )
        rewrites.extend(batch)
        for key in usage:
            usage[key] += int(batch_usage.get(key, 0) or 0)
    return (
        {(item.original_query, item.jurisdiction): item for item in rewrites},
        {
            "status": "ok", "model": settings.llm_model,
            "reasoning_effort": settings.llm_reasoning_effort,
            "request_batches": (len(items) + 9) // 10,
            "wall_time_ms": round((perf_counter() - started) * 1000, 3),
            **usage,
            "estimated_cost_usd": None,
            "cost_note": (
                "Provider pricing is not configured; token usage is measured, cost is not guessed."
            ),
        },
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", default="data/evaluation/rag-cross-lingual-zh-en-v1.json"
    )
    parser.add_argument("--models", nargs="*")
    parser.add_argument(
        "--local-models", nargs="*", default=["jinaai/jina-embeddings-v2-base-zh"]
    )
    parser.add_argument(
        "--sentence-transformer-models", nargs="*", default=[]
    )
    parser.add_argument("--skip-remote", action="store_true")
    parser.add_argument(
        "--output", default="data/benchmarks/cross-language-zh-en-v1.json"
    )
    args = parser.parse_args()
    root = Path(__file__).parents[3]
    settings = get_settings()
    dataset = load_cross_language_dataset(root / args.dataset)
    models = args.models or list(dict.fromkeys(filter(None, [
        settings.embedding_model, "BAAI/bge-m3",
    ])))
    database = Database(settings.database_url)
    database.initialize()
    report = {
        "dataset": dataset["name"],
        "label_status": dataset["label_status"],
        "limitations": dataset["limitations"],
        "embedding_candidates": models,
        "local_embedding_candidates": args.local_models,
        "sentence_transformer_candidates": args.sentence_transformer_models,
        "measurements": [],
        "failures": [],
    }
    rewrites, rewrite_usage = _rewrite_queries(
        settings, dataset["samples"], root / "data/query-cache/cross-language-rewrites.json"
    )
    report["query_rewrite"] = rewrite_usage
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(repository, root / settings.source_dir)
        bm25 = BM25Retriever(repository)
        report["measurements"].append(asdict(
            measure_retriever("bm25", bm25, dataset["samples"])
        ))
        for model in ([] if args.skip_remote else models):
            if not (settings.embedding_base_url and settings.embedding_api_key):
                report["failures"].append({
                    "model": model, "status": "skipped", "reason": "embedding_not_configured"
                })
                continue
            try:
                provider = OpenAICompatibleEmbeddingProvider(
                    base_url=settings.embedding_base_url,
                    api_key=settings.embedding_api_key,
                    model=model,
                    timeout_seconds=settings.embedding_timeout_seconds,
                )
                cached_provider = CachedEmbeddingProvider(provider)
                prewarm = [item["query"] for item in dataset["samples"]]
                for rewrite in rewrites.values():
                    prewarm.extend(rewrite.retrieval_queries())
                cached_provider.embed(list(dict.fromkeys(prewarm)))
                dense = DenseRetriever(repository, cached_provider)
                hybrid = HybridRetriever(repository, dense)
                report["measurements"].extend([
                    asdict(measure_retriever(f"dense:{model}", dense, dataset["samples"])),
                    asdict(measure_retriever(
                        f"hybrid_rrf:{model}", hybrid, dataset["samples"]
                    )),
                ])
                if rewrites:
                    report["measurements"].append(asdict(measure_retriever(
                        f"dense_plus_rewrite_rrf:{model}",
                        RewrittenRetriever(dense, rewrites), dataset["samples"],
                    )))
            except Exception as exc:
                report["failures"].append({
                    "model": model, "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
        for model in args.local_models:
            try:
                cached_provider = CachedEmbeddingProvider(FastEmbedProvider(model))
                prewarm = [item["query"] for item in dataset["samples"]]
                for rewrite in rewrites.values():
                    prewarm.extend(rewrite.retrieval_queries())
                cached_provider.embed(list(dict.fromkeys(prewarm)))
                dense = DenseRetriever(repository, cached_provider)
                hybrid = HybridRetriever(repository, dense)
                report["measurements"].extend([
                    asdict(measure_retriever(
                        f"dense_local:{model}", dense, dataset["samples"]
                    )),
                    asdict(measure_retriever(
                        f"hybrid_rrf_local:{model}", hybrid, dataset["samples"]
                    )),
                ])
                if rewrites:
                    report["measurements"].append(asdict(measure_retriever(
                        f"dense_local_plus_rewrite_rrf:{model}",
                        RewrittenRetriever(dense, rewrites), dataset["samples"],
                    )))
            except Exception as exc:
                report["failures"].append({
                    "model": model, "provider": "fastembed_onnx_local",
                    "status": "failed", "reason": f"{type(exc).__name__}: {exc}",
                })
        for model in args.sentence_transformer_models:
            try:
                cached_provider = CachedEmbeddingProvider(
                    LocalSentenceTransformerProvider(model)
                )
                prewarm = [item["query"] for item in dataset["samples"]]
                for rewrite in rewrites.values():
                    prewarm.extend(rewrite.retrieval_queries())
                cached_provider.embed(list(dict.fromkeys(prewarm)))
                dense = DenseRetriever(repository, cached_provider)
                hybrid = HybridRetriever(repository, dense)
                report["measurements"].extend([
                    asdict(measure_retriever(
                        f"dense_sentence_transformer:{model}", dense, dataset["samples"]
                    )),
                    asdict(measure_retriever(
                        f"hybrid_rrf_sentence_transformer:{model}",
                        hybrid, dataset["samples"]
                    )),
                ])
                if rewrites:
                    report["measurements"].append(asdict(measure_retriever(
                        f"dense_sentence_transformer_plus_rewrite_rrf:{model}",
                        RewrittenRetriever(dense, rewrites), dataset["samples"],
                    )))
            except Exception as exc:
                report["failures"].append({
                    "model": model,
                    "provider": "sentence_transformers_local",
                    "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
    target = root / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
