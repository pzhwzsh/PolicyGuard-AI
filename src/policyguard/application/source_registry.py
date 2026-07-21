"""Validation and coverage reporting for the official source registry."""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    registered: int
    jurisdictions: dict[str, int]
    formats: dict[str, int]
    categories: dict[str, int]
    channels: dict[str, int]


def load_source_registry(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("source_registry_empty")
    ids: set[str] = set()
    urls: set[str] = set()
    for source in sources:
        missing = {
            "id", "jurisdiction", "publisher", "source_url", "format", "activation_policy"
        } - set(source)
        if missing:
            raise ValueError(f"source_registry_missing_fields:{','.join(sorted(missing))}")
        if source["id"] in ids or source["source_url"] in urls:
            raise ValueError("source_registry_duplicate")
        parsed = urlparse(source["source_url"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("source_registry_official_https_required")
        if source["activation_policy"] != "manual_review":
            raise ValueError("source_registry_manual_review_required")
        ids.add(source["id"])
        urls.add(source["source_url"])
    return sources


def source_coverage(sources: list[dict]) -> SourceCoverage:
    return SourceCoverage(
        registered=len(sources),
        jurisdictions=dict(Counter(item["jurisdiction"] for item in sources)),
        formats=dict(Counter(item["format"] for item in sources)),
        categories=dict(Counter(
            category for item in sources for category in item.get("categories", ["all"])
        )),
        channels=dict(Counter(
            channel for item in sources for channel in item.get("channels", ["all"])
        )),
    )
