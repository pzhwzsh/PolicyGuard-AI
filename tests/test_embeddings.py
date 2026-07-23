from pathlib import Path
from types import SimpleNamespace

import policyguard.application.embeddings as embedding_module
from policyguard.application.embeddings import DenseRetriever, configured_embedding_chain
from policyguard.application.hybrid import FallbackRetriever, HybridRetriever
from policyguard.application.knowledge import ingest_source_directory
from policyguard.application.rerank import RerankedHybridRetriever, RerankResult
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository

PROJECT_ROOT = Path(__file__).parents[1]


class FakeEmbeddingProvider:
    provider_name = "fake"
    model_name = "test-v1"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        vectors = []
        for text in texts:
            lowered = text.casefold()
            vectors.append(
                [
                    float("国家级" in text or "最高级" in text),
                    float("truthful" in lowered or "evidence" in lowered),
                    float("misleading" in lowered or "consumer" in lowered),
                ]
            )
        return vectors


class FakeReranker:
    provider_name = "fake-reranker"
    model_name = "rerank-v1"

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        ranked = sorted(
            enumerate(documents),
            key=lambda item: ("国家级" not in item[1], item[0]),
        )
        return [
            RerankResult(index=index, score=1.0 - rank * 0.1)
            for rank, (index, _) in enumerate(ranked[:top_n])
        ]


def test_dense_retriever_uses_persistent_cache(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'embeddings.db').as_posix()}")
    database.initialize()
    provider = FakeEmbeddingProvider()

    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(repository, PROJECT_ROOT / "data" / "sources")
        retriever = DenseRetriever(repository, provider)
        scope = KnowledgeFilter(jurisdiction="CN")

        first = retriever.search("国家级广告", scope=scope)
        calls_after_first = provider.calls
        second = retriever.search("国家级广告", scope=scope)

        assert first[0].chunk.section_id == "article-9"
        assert second[0].chunk.section_id == "article-9"
        assert provider.calls == calls_after_first + 1


def test_rrf_fuses_rankings_without_using_raw_score_scale() -> None:
    database = Database("sqlite:///:memory:")
    database.initialize()
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(repository, PROJECT_ROOT / "data" / "sources")
        provider = FakeEmbeddingProvider()
        hybrid = HybridRetriever(repository, DenseRetriever(repository, provider))
        results = hybrid.search(
            "国家级广告",
            scope=KnowledgeFilter(jurisdiction="CN"),
            top_k=3,
        )
        assert results
        assert results[0].rank == 1
        assert results[0].score > 0


def test_reranked_hybrid_uses_candidate_then_precision_stage(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'rerank.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(repository, PROJECT_ROOT / "data" / "sources")
        provider = FakeEmbeddingProvider()
        reranked = RerankedHybridRetriever(
            repository,
            HybridRetriever(repository, DenseRetriever(repository, provider)),
            FakeReranker(),
        )
        results = reranked.search(
            "国家级广告",
            scope=KnowledgeFilter(jurisdiction="CN"),
            top_k=2,
        )
        assert results[0].chunk.section_id == "article-9"
        assert results[0].rank == 1


def test_runtime_retrieval_falls_back_after_dense_failure() -> None:
    class BrokenRetriever:
        def search(self, query, top_k=5, scope=None):
            raise RuntimeError("embedding offline")

    class WorkingRetriever:
        def search(self, query, top_k=5, scope=None):
            return ["bm25-result"]

    retriever = FallbackRetriever(BrokenRetriever(), WorkingRetriever())

    assert retriever.search("query") == ["bm25-result"]
    assert retriever.last_failures == [
        {"retriever": "BrokenRetriever", "error": "RuntimeError"}
    ]


def test_embedding_chain_survives_primary_initialization_failure(monkeypatch) -> None:
    fallback = FakeEmbeddingProvider()
    monkeypatch.setattr(
        embedding_module,
        "configured_embedding_provider",
        lambda settings: (_ for _ in ()).throw(RuntimeError("primary failed")),
    )
    monkeypatch.setattr(
        embedding_module,
        "configured_embedding_fallback_provider",
        lambda settings: fallback,
    )

    providers, failures = configured_embedding_chain(SimpleNamespace())

    assert providers == [fallback]
    assert failures == [
        {"retriever": "primary_embedding", "error": "RuntimeError"}
    ]


def test_reranker_failure_returns_hybrid_candidates(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'rerank-fallback.db').as_posix()}")
    database.initialize()
    with database.session_factory() as session:
        repository = SqlAlchemyKnowledgeRepository(session)
        ingest_source_directory(repository, PROJECT_ROOT / "data" / "sources")
        provider = FakeEmbeddingProvider()
        hybrid = HybridRetriever(repository, DenseRetriever(repository, provider))

        class BrokenReranker:
            provider_name = "broken"
            model_name = "broken-v1"

            def rerank(self, query, documents, top_n):
                raise RuntimeError("reranker offline")

        retriever = RerankedHybridRetriever(repository, hybrid, BrokenReranker())
        expected = hybrid.search("国家级广告", top_k=2)
        actual = retriever.search("国家级广告", top_k=2)

        assert [item.chunk.id for item in actual] == [item.chunk.id for item in expected]
        assert retriever.last_failure == {
            "reranker": "broken-v1", "error": "RuntimeError",
        }
