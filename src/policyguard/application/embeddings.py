from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx
import numpy as np

from policyguard.application.ports import KnowledgeRepository
from policyguard.domain.models import KnowledgeFilter, SearchHit


class EmbeddingProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class OpenAICompatibleEmbeddingProvider:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 30

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    @property
    def model_name(self) -> str:
        return self.model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        url = self.base_url.rstrip("/") + "/embeddings"
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        data = sorted(payload["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]


class DenseRetriever:
    def __init__(self, repository: KnowledgeRepository, provider: EmbeddingProvider) -> None:
        self.repository = repository
        self.provider = provider

    def search(
        self,
        query: str,
        top_k: int = 5,
        scope: KnowledgeFilter | None = None,
    ) -> list[SearchHit]:
        chunks = self.repository.list_chunks(scope)
        if not chunks:
            return []
        cached = self.repository.load_embeddings(
            [chunk.id for chunk in chunks],
            self.provider.provider_name,
            self.provider.model_name,
        )
        missing = [chunk for chunk in chunks if chunk.id not in cached]
        if missing:
            index_missing_embeddings(self.repository, self.provider, missing)
            cached.update(self.repository.load_embeddings(
                [chunk.id for chunk in missing],
                self.provider.provider_name,
                self.provider.model_name,
            ))
        embed_query = getattr(self.provider, "embed_query", self.provider.embed)
        query_vector = np.asarray(embed_query([query])[0], dtype=float)
        scored: list[tuple[int, float]] = []
        for index, chunk in enumerate(chunks):
            vector = np.asarray(cached[chunk.id], dtype=float)
            denominator = np.linalg.norm(query_vector) * np.linalg.norm(vector)
            score = float(np.dot(query_vector, vector) / denominator) if denominator else 0.0
            if score > 0:
                scored.append((index, score))
        scored.sort(key=lambda item: (-item[1], chunks[item[0]].id))
        return [
            SearchHit(chunk=chunks[index], score=round(score, 6), rank=rank)
            for rank, (index, score) in enumerate(scored[:top_k], start=1)
        ]


def index_missing_embeddings(
    repository, provider: EmbeddingProvider, chunks=None, batch_size=64
) -> int:
    chunks = list(chunks if chunks is not None else repository.list_chunks())
    cached = repository.load_embeddings(
        [chunk.id for chunk in chunks], provider.provider_name, provider.model_name
    )
    missing = [chunk for chunk in chunks if chunk.id not in cached]
    indexed = 0
    for offset in range(0, len(missing), batch_size):
        batch = missing[offset : offset + batch_size]
        vectors = provider.embed([f"{chunk.heading}\n{chunk.text}" for chunk in batch])
        if len(vectors) != len(batch):
            raise ValueError("embedding_provider_length_mismatch")
        repository.save_embeddings(
            {chunk.id: vector for chunk, vector in zip(batch, vectors, strict=True)},
            provider.provider_name,
            provider.model_name,
        )
        indexed += len(batch)
    return indexed


@lru_cache(maxsize=4)
def configured_embedding_provider(settings) -> EmbeddingProvider | None:
    if getattr(settings, "app_env", "") == "test":
        return None
    if settings.embedding_provider == "fastembed":
        if not settings.embedding_model:
            return None
        from policyguard.application.onnx_embeddings import FastEmbedProvider

        return FastEmbedProvider(settings.embedding_model)
    if not (
        settings.embedding_base_url and settings.embedding_api_key and settings.embedding_model
    ):
        return None
    if settings.embedding_provider != "openai_compatible":
        return None
    return OpenAICompatibleEmbeddingProvider(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
