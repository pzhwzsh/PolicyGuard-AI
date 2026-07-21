import argparse
from pathlib import Path

from policyguard.application.benchmark import run_embedding_benchmark, write_results
from policyguard.application.onnx_embeddings import FastEmbedProvider
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository

MODELS = [
    "BAAI/bge-small-zh-v1.5",
    "jinaai/jina-embeddings-v2-base-zh",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/evaluation/rag-baseline.json")
    parser.add_argument("--output", default="data/benchmarks/local-embedding-results.json")
    parser.add_argument("--models", nargs="+", default=MODELS)
    args = parser.parse_args()
    root = Path(__file__).parents[3]
    database = Database("sqlite:///./data/policyguard.db")
    database.initialize()

    def factory(candidate):
        return FastEmbedProvider(candidate["model"])

    matrix = root / "data" / "benchmarks" / "local-embedding-matrix.json"
    matrix.parent.mkdir(parents=True, exist_ok=True)
    matrix.write_text(
        __import__("json").dumps({"embedding": [{"model": model} for model in args.models]}),
        encoding="utf-8",
    )
    with database.session_factory() as session:
        results = run_embedding_benchmark(
            SqlAlchemyKnowledgeRepository(session),
            factory,
            matrix,
            root / args.dataset,
        )
    output = root / args.output
    write_results(results, output)
    for result in results:
        summary = (
            f"{result.model}: {result.status} "
            f"hit@5={result.hit_rate_at_k:.3f} "
            f"mrr={result.mean_reciprocal_rank:.3f}"
        )
        print(summary)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
