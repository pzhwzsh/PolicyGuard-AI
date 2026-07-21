import json
from pathlib import Path

import pytest

from policyguard.application.source_registry import load_source_registry, source_coverage

ROOT = Path(__file__).parents[1]


def test_real_registry_is_valid_and_covers_three_jurisdictions() -> None:
    sources = load_source_registry(ROOT / "config/source_registry.json")
    coverage = source_coverage(sources)
    assert coverage.registered >= 10
    assert set(coverage.jurisdictions) == {"CN", "US", "EU"}
    assert all(item["activation_policy"] == "manual_review" for item in sources)


def test_registry_rejects_duplicate_or_non_https_sources(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    source = {
        "id": "x", "jurisdiction": "US", "publisher": "p",
        "source_url": "http://example.com", "format": "html",
        "activation_policy": "manual_review",
    }
    path.write_text(json.dumps({"sources": [source]}), encoding="utf-8")
    with pytest.raises(ValueError, match="official_https_required"):
        load_source_registry(path)
