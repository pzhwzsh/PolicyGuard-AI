import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from policyguard.application.ports import KnowledgeRepository
from policyguard.domain.models import (
    KnowledgeFilter,
    PolicyDocument,
    PolicyScope,
    PolicySection,
    SearchHit,
)


TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+")


def tokenize_zh(text: str) -> list[str]:
    """Dependency-free lexical baseline using Latin words plus CJK unigrams/bigrams."""
    tokens: list[str] = []
    for item in TOKEN_PATTERN.findall(text.casefold()):
        if all("\u4e00" <= char <= "\u9fff" for char in item):
            tokens.extend(item)
            tokens.extend(item[index : index + 2] for index in range(len(item) - 1))
        else:
            tokens.append(item)
    return tokens


def load_policy_document(path: Path) -> PolicyDocument:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sections = tuple(
        PolicySection(
            section_id=item["section_id"],
            heading=item["heading"],
            text=item["text"],
        )
        for item in payload["sections"]
    )
    scopes = tuple(
        PolicyScope(
            jurisdiction=item["jurisdiction"],
            category=item.get("category", "all"),
            channel=item.get("channel", "all"),
            legal_level=item.get("legal_level", "guidance"),
            source_language=item.get("source_language", "en"),
            translation_status=item.get("translation_status", "original"),
            effective_from=item.get("effective_from", payload["published_at"]),
            effective_to=item.get("effective_to"),
        )
        for item in payload.get("scopes", [])
    )
    canonical_content = "\n".join(
        f"{section.section_id}|{section.heading}|{section.text}" for section in sections
    )
    content_hash = sha256(canonical_content.encode("utf-8")).hexdigest()
    document_id = sha256(
        f"{payload['source_url']}|{payload['version']}".encode("utf-8")
    ).hexdigest()
    return PolicyDocument(
        id=document_id,
        title=payload["title"],
        source_url=payload["source_url"],
        publisher=payload["publisher"],
        version=payload["version"],
        published_at=payload["published_at"],
        retrieved_at=payload["retrieved_at"],
        content_hash=content_hash,
        sections=sections,
        scopes=scopes,
    )


def ingest_source_directory(repository: KnowledgeRepository, source_dir: Path) -> int:
    if not source_dir.exists():
        return 0
    ingested = 0
    for path in sorted(source_dir.glob("*.json")):
        repository.upsert_document(load_policy_document(path))
        ingested += 1
    return ingested


class BM25Retriever:
    def __init__(self, repository: KnowledgeRepository, k1: float = 1.5, b: float = 0.75) -> None:
        self.repository = repository
        self.k1 = k1
        self.b = b

    def search(
        self,
        query: str,
        top_k: int = 5,
        scope: KnowledgeFilter | None = None,
    ) -> list[SearchHit]:
        chunks = self.repository.list_chunks(scope)
        if not chunks:
            return []
        documents = [tokenize_zh(f"{chunk.heading} {chunk.text}") for chunk in chunks]
        query_tokens = set(tokenize_zh(query))
        if not query_tokens:
            return []
        average_length = sum(map(len, documents)) / len(documents)
        document_frequency = Counter(
            token for document in documents for token in set(document)
        )
        scored: list[tuple[int, float]] = []
        for index, document in enumerate(documents):
            frequencies = Counter(document)
            score = sum(
                self._term_score(
                    frequency=frequencies[token],
                    document_length=len(document),
                    average_length=average_length,
                    document_frequency=document_frequency[token],
                    document_count=len(documents),
                )
                for token in query_tokens
                if frequencies[token]
            )
            if score > 0:
                scored.append((index, score))
        scored.sort(key=lambda item: (-item[1], chunks[item[0]].id))
        return [
            SearchHit(chunk=chunks[index], score=round(score, 6), rank=rank)
            for rank, (index, score) in enumerate(scored[:top_k], start=1)
        ]

    def _term_score(
        self,
        *,
        frequency: int,
        document_length: int,
        average_length: float,
        document_frequency: int,
        document_count: int,
    ) -> float:
        inverse_document_frequency = math.log(
            1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
        )
        denominator = frequency + self.k1 * (
            1 - self.b + self.b * document_length / average_length
        )
        return inverse_document_frequency * frequency * (self.k1 + 1) / denominator


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    sample_count: int
    hit_rate_at_k: float
    mean_reciprocal_rank: float


def evaluate_retriever(
    retriever: BM25Retriever,
    dataset_path: Path,
    top_k: int = 5,
) -> RetrievalMetrics:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    reciprocal_ranks: list[float] = []
    hits = 0
    for sample in dataset["samples"]:
        scope = (
            KnowledgeFilter(
                jurisdiction=sample["jurisdiction"],
                category=sample.get("category", "all"),
                channel=sample.get("channel", "all"),
                as_of=sample.get("as_of"),
            )
            if sample.get("jurisdiction")
            else None
        )
        results = retriever.search(sample["query"], top_k=top_k, scope=scope)
        expected = sample["expected_section_id"]
        matching_ranks = [hit.rank for hit in results if hit.chunk.section_id == expected]
        if matching_ranks:
            hits += 1
            reciprocal_ranks.append(1 / matching_ranks[0])
        else:
            reciprocal_ranks.append(0.0)
    sample_count = len(dataset["samples"])
    return RetrievalMetrics(
        sample_count=sample_count,
        hit_rate_at_k=hits / sample_count if sample_count else 0.0,
        mean_reciprocal_rank=(
            sum(reciprocal_ranks) / sample_count if sample_count else 0.0
        ),
    )
