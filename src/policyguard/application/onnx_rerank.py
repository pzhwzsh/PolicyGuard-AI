"""Optional FastEmbed cross-encoder reranker."""

from dataclasses import dataclass

from policyguard.application.rerank import RerankResult


@dataclass
class FastEmbedReranker:
    model: str

    def __post_init__(self) -> None:
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as exc:
            raise RuntimeError("install policyguard-ai[local-embedding-onnx] first") from exc
        self._encoder = TextCrossEncoder(model_name=self.model)

    @property
    def provider_name(self) -> str:
        return "fastembed_onnx_local"

    @property
    def model_name(self) -> str:
        return self.model

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        ranked = self._encoder.rerank(query, documents)
        ordered = sorted(enumerate(ranked), key=lambda item: item[1], reverse=True)
        return [RerankResult(index=index, score=float(score)) for index, score in ordered[:top_n]]
