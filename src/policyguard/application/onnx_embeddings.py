"""CPU-first ONNX embedding provider that does not require PyTorch."""

import os
from dataclasses import dataclass


@dataclass
class FastEmbedProvider:
    model: str

    def __post_init__(self) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError("install policyguard-ai[local-embedding-onnx] first") from exc
        self._encoder = TextEmbedding(
            model_name=self.model,
            cache_dir=os.getenv("FASTEMBED_CACHE_DIR") or None,
        )

    @property
    def provider_name(self) -> str:
        return "fastembed_onnx_local"

    @property
    def model_name(self) -> str:
        return self.model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._encoder.embed(texts)]

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._encoder.query_embed(texts)]
