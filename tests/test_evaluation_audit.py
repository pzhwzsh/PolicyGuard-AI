import json
from pathlib import Path

from policyguard.application.evaluation_audit import (
    audit_holdout_dataset,
    classify_retrieval_failures,
)


def write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_holdout_audit_blocks_leakage_and_pending_labels(tmp_path: Path) -> None:
    development = write(tmp_path / "dev.json", {"samples": [{"query": "same question"}]})
    holdout = write(
        tmp_path / "holdout.json",
        {
            "name": "holdout",
            "split": "holdout",
            "frozen_at": "2026-07-23",
            "minimum_sample_count": 1,
            "samples": [
                {
                    "id": "h1",
                    "query": " Same   Question ",
                    "jurisdiction": "US",
                    "answerable": True,
                    "review_status": "pending",
                }
            ],
        },
    )
    report = audit_holdout_dataset(holdout, [development])
    assert report["development_leakage_sample_ids"] == ["h1"]
    assert report["reviewed_count"] == 0
    assert report["ready_for_reportable_holdout_metrics"] is False


def test_failure_classifier_separates_recall_ranking_and_false_answers() -> None:
    report = classify_retrieval_failures(
        [
            {
                "id": "a",
                "answerable": True,
                "expected_section_id": "s1",
                "retrieved_section_ids": [],
            },
            {
                "id": "b",
                "answerable": True,
                "expected_section_id": "s1",
                "retrieved_section_ids": ["s2", "s1"],
            },
            {
                "id": "c",
                "answerable": False,
                "expected_section_id": None,
                "retrieved_section_ids": ["s2"],
                "predicted_answerable": True,
            },
        ]
    )
    assert report["counts"] == {"false_answer": 1, "ranking_miss": 1, "recall_miss": 1}


def test_completed_isolated_holdout_becomes_reportable(tmp_path: Path) -> None:
    development = write(tmp_path / "dev.json", {"samples": [{"query": "development"}]})
    holdout = write(
        tmp_path / "holdout.json",
        {
            "name": "holdout",
            "split": "holdout",
            "frozen_at": "2026-07-23",
            "minimum_sample_count": 1,
            "independent_reviewer_attestation": True,
            "samples": [
                {
                    "id": "h1",
                    "query": "new question",
                    "jurisdiction": "EU",
                    "answerable": True,
                    "expected_section_id": "article-1",
                    "review_status": "independent_human_reviewed",
                    "reviewer_alias": "reviewer-1",
                    "reviewed_at": "2026-07-23",
                }
            ],
        },
    )
    assert audit_holdout_dataset(holdout, [development])[
        "ready_for_reportable_holdout_metrics"
    ]
