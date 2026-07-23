from dataclasses import dataclass
from typing import Protocol

from policyguard.application.hybrid import HybridRetriever
from policyguard.application.ports import KnowledgeRepository
from policyguard.application.provider_http import (
    ProviderRetryPolicy,
    post_with_retry,
    should_try_backup,
)
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
    retry_policy: ProviderRetryPolicy = ProviderRetryPolicy()

    @property
    def provider_name(self) -> str:
        return "cohere_compatible"

    @property
    def model_name(self) -> str:
        return self.model

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        response = post_with_retry(
            self.base_url.rstrip("/") + "/rerank",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "query": query, "documents": documents, "top_n": top_n},
            timeout=self.timeout_seconds,
            policy=self.retry_policy,
        )
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
        self.last_failure: dict[str, str] | None = None

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
        self.last_failure = None
        try:
            ranked = self.reranker.rerank(
                query,
                [f"{hit.chunk.heading}\n{hit.chunk.text}" for hit in candidates],
                top_n=top_k,
            )
        except Exception as exc:
            self.last_failure = {
                "reranker": self.reranker.model_name,
                "error": type(exc).__name__,
            }
            return [
                SearchHit(chunk=hit.chunk, score=hit.score, rank=rank)
                for rank, hit in enumerate(candidates[:top_k], start=1)
            ]
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


class FallbackReranker:
    provider_name = "fallback_chain"

    def __init__(self, *providers: Reranker) -> None:
        self.providers = providers
        self.model_name = "->".join(provider.model_name for provider in providers)
        self.last_model: str | None = None

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        for provider in self.providers:
            try:
                result = provider.rerank(query, documents, top_n)
                self.last_model = provider.model_name
                return result
            except Exception as exc:
                if not should_try_backup(exc):
                    raise
                continue
        raise RuntimeError("all_rerankers_failed")


def configured_reranker(settings) -> Reranker | None:
    if not (settings.rerank_base_url and settings.rerank_api_key and settings.rerank_model):
        return None
    policy = ProviderRetryPolicy(
        settings.provider_max_attempts, settings.provider_backoff_seconds
    )
    models = [settings.rerank_model]
    if settings.rerank_fallback_model and settings.rerank_fallback_model not in models:
        models.append(settings.rerank_fallback_model)
    providers = tuple(
        CohereCompatibleReranker(
            base_url=settings.rerank_base_url,
            api_key=settings.rerank_api_key,
            model=model,
            timeout_seconds=settings.rerank_timeout_seconds,
            retry_policy=policy,
        )
        for model in models
    )
    return providers[0] if len(providers) == 1 else FallbackReranker(*providers)
