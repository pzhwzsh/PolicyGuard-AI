import json
import shutil
from pathlib import Path

from policyguard.application.portfolio_benchmark import (
    build_marketing_cases,
    build_rag_holdout,
    build_remediation_dataset,
    evaluate_concurrent_workflows,
    run_portfolio_benchmark,
)

ROOT = Path(__file__).parents[1]


def test_portfolio_datasets_have_declared_scale_and_no_exact_overlap() -> None:
    rag = build_rag_holdout()
    remediation = build_remediation_dataset()
    campaigns = build_marketing_cases()
    existing = set()
    for name in ("rag-baseline.json", "rag-hard-v1.json", "rag-no-answer-v1.json"):
        payload = json.loads((ROOT / "data/evaluation" / name).read_text(encoding="utf-8"))
        existing.update(item["query"] for item in payload["samples"])
    assert len(rag["samples"]) == 120
    assert rag["legal_quality_metric_eligible"] is False
    assert rag["split"] == "synthetic_stress_set_not_a_legal_holdout"
    assert len({item["query"] for item in rag["samples"]}) == 120
    assert not existing.intersection(item["query"] for item in rag["samples"])
    assert len(remediation["samples"]) == 100
    assert len({item["before"] for item in remediation["samples"]}) == 100
    assert len(campaigns) == 100
    assert all(item["review_status"] == "pending_human_review" for item in campaigns)


def test_portfolio_benchmark_runs_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    shutil.copytree(ROOT / "data/sources", tmp_path / "data/sources")
    shutil.copytree(ROOT / "data/evidence", tmp_path / "data/evidence")
    report = run_portfolio_benchmark(tmp_path)
    assert report["rag"]["sample_count"] == 120
    assert report["rag"]["metric_status"] == "synthetic_stress_only_not_legal_quality_evidence"
    assert report["remediation"]["sample_count"] == 100
    assert report["workflow"]["case_count"] == 100
    assert report["workflow"]["event_count"] >= 500
    assert len(report["workflow"]["outcomes"]) == 100
    assert len(report["rag"]["predictions"]) == 120
    assert report["workflow"]["traffic_type"].startswith("synthetic")
    assert report["concurrency"]["workers"] == 8
    assert report["concurrency"]["success_rate"] == 1.0
    packet = json.loads(
        (tmp_path / "data/evaluation/human-review-packet-v1.json").read_text(encoding="utf-8")
    )
    assert packet["status"] == "pending_real_human_review"
    assert packet["counts"]["completed_decisions"] == 0
    assert len(packet["dataset_reviews"]) == 320


def test_postgres_benchmark_requires_isolated_database_name(tmp_path: Path) -> None:
    try:
        evaluate_concurrent_workflows(
            tmp_path,
            [],
            database_url="postgresql+psycopg://localhost/policyguard",
            backend="postgresql",
        )
    except ValueError as exc:
        assert str(exc) == "benchmark_database_url_must_contain_benchmark"
    else:
        raise AssertionError("unsafe benchmark database URL was accepted")
