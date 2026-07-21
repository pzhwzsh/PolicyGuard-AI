from pathlib import Path

from policyguard.application.benchmark import run_embedding_benchmark, write_results
from policyguard.application.embeddings import OpenAICompatibleEmbeddingProvider
from policyguard.config import get_settings
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def main() -> None:
    settings = get_settings()
    if not (settings.embedding_base_url and settings.embedding_api_key):
        print("SKIPPED: configure EMBEDDING_BASE_URL and EMBEDDING_API_KEY before running real model benchmarks.")
        return
    root = Path(__file__).parents[3]
    database = Database(settings.database_url)
    database.initialize()

    def factory(candidate):
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=candidate["model"],
            timeout_seconds=settings.embedding_timeout_seconds,
        )

    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        results = run_embedding_benchmark(
            repository, factory, root / "config" / "model_matrix.json",
            root / settings.evaluation_dataset,
        )
    output = root / "data" / "benchmarks" / "embedding-results.json"
    write_results(results, output)
    for result in results:
        print(f"{result.model}: {result.status} hit@5={result.hit_rate_at_k:.3f} mrr={result.mean_reciprocal_rank:.3f}")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
