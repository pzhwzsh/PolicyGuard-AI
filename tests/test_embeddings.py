from pathlib import Path

from policyguard.application.embeddings import DenseRetriever
from policyguard.application.hybrid import HybridRetriever
from policyguard.application.rerank import RerankResult, RerankedHybridRetriever
from policyguard.application.knowledge import ingest_source_directory
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
        return [RerankResult(index=index, score=1.0 - rank * 0.1) for rank, (index, _) in enumerate(ranked[:top_n])]


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
