import argparse
import json
import time
from pathlib import Path
from statistics import mean

from policyguard.application.knowledge import BM25Retriever, ingest_source_directory
from policyguard.application.onnx_rerank import FastEmbedReranker
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository

MODELS = ["BAAI/bge-reranker-base", "jinaai/jina-reranker-v2-base-multilingual"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/evaluation/rag-baseline.json")
    parser.add_argument("--output", default="data/benchmarks/local-rerank-results.json")
    parser.add_argument("--models", nargs="+", default=MODELS)
    args = parser.parse_args()
    root = Path(__file__).parents[3]
    dataset = json.loads((root / args.dataset).read_text(encoding="utf-8"))
    database = Database("sqlite:///./data/policyguard.db")
    database.initialize()
    results = []
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(repository, root / "data/sources")
        lexical = BM25Retriever(repository)
        for model in args.models:
            try:
                reranker = FastEmbedReranker(model)
                latencies = []
                hits = 0
                reciprocal = 0.0
                for sample in dataset["samples"]:
                    scope = KnowledgeFilter(
                        jurisdiction=sample["jurisdiction"],
                        category=sample.get("category", "all"),
                        channel=sample.get("channel", "all"),
                        as_of=sample.get("as_of"),
                    )
                    candidates = lexical.search(sample["query"], top_k=10, scope=scope)
                    started = time.perf_counter()
                    ranked = reranker.rerank(
                        sample["query"],
                        [f"{item.chunk.heading}\n{item.chunk.text}" for item in candidates],
                        5,
                    )
                    latencies.append((time.perf_counter() - started) * 1000)
                    ranks = [
                        rank
                        for rank, item in enumerate(ranked, start=1)
                        if candidates[item.index].chunk.section_id
                        == sample["expected_section_id"]
                    ]
                    if ranks:
                        hits += 1
                        reciprocal += 1 / ranks[0]
                count = len(dataset["samples"])
                results.append(
                    {
                        "model": model,
                        "status": "ok",
                        "hit_rate_at_5": hits / count,
                        "mrr": reciprocal / count,
                        "mean_latency_ms": mean(latencies),
                        "p95_latency_ms": sorted(latencies)[
                            min(len(latencies) - 1, int(len(latencies) * 0.95))
                        ],
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "model": model,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
