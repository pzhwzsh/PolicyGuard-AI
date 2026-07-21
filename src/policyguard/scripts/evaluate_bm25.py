from pathlib import Path

from policyguard.application.knowledge import BM25Retriever, evaluate_retriever
from policyguard.config import get_settings
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def main() -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    database.initialize()
    with database.session_factory() as session:
        metrics = evaluate_retriever(
            BM25Retriever(SqlAlchemyKnowledgeRepository(session)),
            Path(settings.evaluation_dataset),
            top_k=5,
        )
    print(f"samples={metrics.sample_count}")
    print(f"hit_rate_at_5={metrics.hit_rate_at_k:.4f}")
    print(f"mrr={metrics.mean_reciprocal_rank:.4f}")


if __name__ == "__main__":
    main()

