"""Optional local SentenceTransformer embeddings for providers without /embeddings."""

from dataclasses import dataclass


@dataclass
class LocalSentenceTransformerProvider:
    model: str
    device: str = "cpu"

    def __post_init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("install policyguard-ai[local-embedding] first") from exc
        self._encoder = SentenceTransformer(self.model, device=self.device)

    @property
    def provider_name(self) -> str:
        return "sentence_transformers_local"

    @property
    def model_name(self) -> str:
        return self.model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        kwargs = {
            "normalize_embeddings": True,
            "convert_to_numpy": True,
            "show_progress_bar": False,
        }
        if "e5" in self.model.casefold():
            texts = [f"passage: {text}" for text in texts]
        return self._encoder.encode(texts, **kwargs).tolist()

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        if "e5" not in self.model.casefold():
            return self.embed(texts)
        return self._encoder.encode(
            [f"query: {text}" for text in texts],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).tolist()
