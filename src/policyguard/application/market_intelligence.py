import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from policyguard.application.knowledge import tokenize_zh


class MarketIntelligenceRetriever:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    @classmethod
    def from_path(cls, path: Path) -> "MarketIntelligenceRetriever":
        if not path.exists():
            return cls([])
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(list(payload.get("records", [])))

    def search(
        self, query: str, jurisdictions: list[str], *, category: str = "all", top_k: int = 4
    ) -> list[dict[str, Any]]:
        allowed = {item.upper() for item in jurisdictions} & {"EU", "US"}
        candidates = [
            item for item in self.records
            if item.get("jurisdiction", "").upper() in allowed
            and item.get("category", "all") in {"all", category.casefold(), "hvac"}
        ]
        query_tokens = set(tokenize_zh(query))
        if not candidates or not query_tokens:
            return []
        documents = [tokenize_zh(self._search_text(item)) for item in candidates]
        frequencies = Counter(token for document in documents for token in set(document))
        scored: list[tuple[float, dict[str, Any]]] = []
        for record, document in zip(candidates, documents, strict=True):
            counts = Counter(document)
            score = sum(
                (1 + math.log1p(counts[token]))
                * math.log(1 + (len(documents) + 1) / (frequencies[token] + 1))
                for token in query_tokens if counts[token]
            )
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        return [record | {"score": round(score, 6)} for score, record in scored[:top_k]]

    @staticmethod
    def _search_text(record: dict[str, Any]) -> str:
        return " ".join(
            [
                str(record.get("title", "")),
                str(record.get("regulatory_fact", "")),
                str(record.get("opportunity", "")),
                str(record.get("caveat", "")),
                " ".join(record.get("keywords", [])),
            ]
        )
