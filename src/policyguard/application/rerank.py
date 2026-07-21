from dataclasses import dataclass
from typing import Protocol

import httpx

from policyguard.application.hybrid import HybridRetriever
from policyguard.application.ports import KnowledgeRepository
from policyguard.domain.models import KnowledgeFilter, SearchHit


@dataclass(frozen=True, slots=True)
class RerankResult:
    index: int
    score: float


class Reranker(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]: ...


@dataclass(frozen=True, slots=True)
class CohereCompatibleReranker:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 30

    @property
    def provider_name(self) -> str:
        return "cohere_compatible"

    @property
    def model_name(self) -> str:
        return self.model

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        response = httpx.post(
            self.base_url.rstrip("/") + "/rerank",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "query": query, "documents": documents, "top_n": top_n},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return [
            RerankResult(index=item["index"], score=float(item["relevance_score"]))
            for item in payload["results"]
        ]


class RerankedHybridRetriever:
    def __init__(
        self,
        repository: KnowledgeRepository,
        hybrid: HybridRetriever,
        reranker: Reranker,
    ) -> None:
        self.repository = repository
        self.hybrid = hybrid
        self.reranker = reranker

    def search(
        self,
        query: str,
        top_k: int = 5,
        candidate_k: int = 20,
        scope: KnowledgeFilter | None = None,
    ) -> list[SearchHit]:
        candidates = self.hybrid.search(query, top_k=candidate_k, scope=scope)
        if not candidates:
            return []
        ranked = self.reranker.rerank(
            query,
            [f"{hit.chunk.heading}\n{hit.chunk.text}" for hit in candidates],
            top_n=top_k,
        )
        if any(item.index < 0 or item.index >= len(candidates) for item in ranked):
            raise ValueError("reranker_index_out_of_range")
        return [
            SearchHit(
                chunk=candidates[item.index].chunk,
                score=round(item.score, 6),
                rank=rank,
            )
            for rank, item in enumerate(ranked[:top_k], start=1)
        ]


def configured_reranker(settings) -> CohereCompatibleReranker | None:
    if not (settings.rerank_base_url and settings.rerank_api_key and settings.rerank_model):
        return None
    return CohereCompatibleReranker(
        base_url=settings.rerank_base_url,
        api_key=settings.rerank_api_key,
        model=settings.rerank_model,
        timeout_seconds=settings.rerank_timeout_seconds,
    )

