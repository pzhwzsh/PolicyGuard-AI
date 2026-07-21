import json
from dataclasses import asdict
from pathlib import Path

from policyguard.application.embeddings import DenseRetriever, configured_embedding_provider
from policyguard.application.knowledge import BM25Retriever, evaluate_retriever
from policyguard.config import get_settings
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def main() -> None:
    root = Path(__file__).parents[3]
    dataset = root / "data/evaluation/rag-robustness-v1.json"
    settings = get_settings()
    database = Database(settings.database_url)
    database.initialize()
    results = {}
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        results["bm25"] = asdict(
            evaluate_retriever(BM25Retriever(repository), dataset, top_k=5)
        )
        provider = configured_embedding_provider(settings)
        if provider:
            results["dense"] = asdict(evaluate_retriever(
                DenseRetriever(repository, provider), dataset, top_k=5
            ))
    target = root / "data/benchmarks/rag-robustness.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
