from pathlib import Path

from policyguard.application.abstention_calibration import calibrate_abstention
from policyguard.application.remediation_evaluation import evaluate_remediation_dataset


ROOT = Path(__file__).parents[1]


def test_abstention_calibration_balances_recall_and_false_answers() -> None:
    report = calibrate_abstention([
        {"answerable": True, "top_score": 0.8, "expected_in_top_k": True},
        {"answerable": True, "top_score": 0.6, "expected_in_top_k": True},
        {"answerable": False, "top_score": 0.7, "expected_in_top_k": False},
        {"answerable": False, "top_score": 0.2, "expected_in_top_k": False},
    ], [0.0, 0.5, 0.75, 0.9])
    assert report["best"].threshold == 0.5
    assert report["development_only"] is True


def test_remediation_quality_metrics_cover_spans_citations_and_facts() -> None:
    report = evaluate_remediation_dataset(
        ROOT / "data/evaluation/remediation-quality-v1.json"
    )
    assert report["sample_count"] == 6
    assert report["claim_span_accuracy"] == 1.0
    assert report["citation_section_accuracy"] == 1.0
    assert report["protected_fact_retention"] == 1.0
    assert report["residual_baseline_pass_rate"] == 1.0
    assert report["external_side_effect"] is False
