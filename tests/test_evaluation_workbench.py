import json
from pathlib import Path

import pytest

from policyguard.application.evaluation_workbench import (
    dataset_fingerprint,
    run_retrieval_evaluation,
    validate_evaluation_path,
)


def test_evaluation_dataset_is_scoped_and_hashed(tmp_path: Path) -> None:
    dataset = tmp_path / "data/evaluation/test.json"
    dataset.parent.mkdir(parents=True)
    dataset.write_text(json.dumps({"samples": [{"query": "q"}]}), encoding="utf-8")
    selected = validate_evaluation_path(tmp_path, "data/evaluation/test.json")
    assert selected == dataset
    assert len(dataset_fingerprint(selected)) == 64


def test_evaluation_dataset_rejects_paths_outside_evaluation_root(tmp_path: Path) -> None:
    outside = tmp_path / "private.json"
    outside.write_text(json.dumps({"samples": [{"query": "q"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="evaluation_dataset_invalid"):
        validate_evaluation_path(tmp_path, "private.json")


def test_bge_m3_uses_sentence_transformer_provider(tmp_path: Path, monkeypatch) -> None:
    dataset = tmp_path / "data/evaluation/test.json"
    dataset.parent.mkdir(parents=True)
    dataset.write_text(json.dumps({"samples": [{
        "query": "q", "jurisdiction": "CN", "expected_section_id": "s"
    }]}), encoding="utf-8")

    class Provider:
        provider_name = "sentence_transformers_local"

    class Retriever:
        def __init__(self, repository, provider):
            assert isinstance(provider, Provider)

    class Metrics:
        sample_count = 1
        hit_rate_at_k = 1.0
        mean_reciprocal_rank = 1.0

    monkeypatch.setattr(
        "policyguard.application.evaluation_workbench.LocalSentenceTransformerProvider",
        lambda model: Provider(),
    )
    monkeypatch.setattr(
        "policyguard.application.evaluation_workbench.DenseRetriever", Retriever
    )
    monkeypatch.setattr(
        "policyguard.application.evaluation_workbench.evaluate_retriever",
        lambda retriever, path, top_k: Metrics(),
    )

    result = run_retrieval_evaluation(
        object(), root=tmp_path, dataset="data/evaluation/test.json",
        candidates=["BAAI/bge-m3"], top_k=5,
        thresholds={"min_hit_rate_at_k": 0.0, "min_mrr": 0.0, "max_latency_ms": 1000.0},
    )
    assert result["results"][0]["provider"] == "sentence_transformers_local"
