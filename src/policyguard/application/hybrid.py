from policyguard.application.embeddings import DenseRetriever
from policyguard.application.knowledge import BM25Retriever
from policyguard.application.ports import KnowledgeRepository
from policyguard.domain.models import KnowledgeFilter, SearchHit


def reciprocal_rank_fusion(
    result_sets: list[list[SearchHit]], top_k: int = 5, smoothing: int = 60
) -> list[SearchHit]:
    """Fuse rankings without pretending BM25 and cosine scores share a scale."""
    by_chunk: dict[str, tuple[SearchHit, float]] = {}
    for results in result_sets:
        for hit in results:
            existing = by_chunk.get(hit.chunk.id)
            fused_score = 1 / (smoothing + hit.rank)
            if existing is None:
                by_chunk[hit.chunk.id] = (hit, fused_score)
            else:
                by_chunk[hit.chunk.id] = (existing[0], existing[1] + fused_score)
    ranked = sorted(by_chunk.values(), key=lambda item: (-item[1], item[0].chunk.id))
    return [
        SearchHit(chunk=hit.chunk, score=round(score, 6), rank=rank)
        for rank, (hit, score) in enumerate(ranked[:top_k], start=1)
    ]


class HybridRetriever:
    def __init__(self, repository: KnowledgeRepository, dense: DenseRetriever) -> None:
        self.repository = repository
        self.lexical = BM25Retriever(repository)
        self.dense = dense

    def search(
        self,
        query: str,
        top_k: int = 5,
        scope: KnowledgeFilter | None = None,
    ) -> list[SearchHit]:
        return reciprocal_rank_fusion(
            [
                self.lexical.search(query, top_k=top_k, scope=scope),
                self.dense.search(query, top_k=top_k, scope=scope),
            ],
            top_k=top_k,
        )

