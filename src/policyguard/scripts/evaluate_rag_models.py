import json
from pathlib import Path
from time import perf_counter

from policyguard.application.embeddings import DenseRetriever, configured_embedding_provider
from policyguard.application.evaluation_suite import evaluate_suite
from policyguard.application.hybrid import HybridRetriever
from policyguard.application.knowledge import BM25Retriever
from policyguard.application.rerank import RerankedHybridRetriever, configured_reranker
from policyguard.config import get_settings
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def main() -> None:
    root = Path(__file__).parents[3]
    settings = get_settings()
    database = Database(settings.database_url)
    database.initialize()
    reports = {}
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        retrievers = {"bm25": BM25Retriever(repository)}
        provider = configured_embedding_provider(settings)
        if provider:
            dense = DenseRetriever(repository, provider)
            hybrid = HybridRetriever(repository, dense)
            retrievers["dense"] = dense
            retrievers["hybrid_rrf"] = hybrid
            reranker = configured_reranker(settings)
            if reranker:
                retrievers["hybrid_rerank"] = RerankedHybridRetriever(
                    repository, hybrid, reranker
                )
        for name, retriever in retrievers.items():
            started = perf_counter()
            report = evaluate_suite(
                retriever, root / "data/evaluation/suite-v1.json"
            )
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            reports[name] = report
    target = root / "data/benchmarks/rag-suite-models.json"
    target.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
