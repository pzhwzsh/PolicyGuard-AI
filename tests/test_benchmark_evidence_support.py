from policyguard.application.evidence_support import EvidenceDecision
from policyguard.scripts.benchmark_evidence_support import calculate_quality_metrics


def decision(case_id: str, supported: bool) -> EvidenceDecision:
    return EvidenceDecision(case_id, supported, "quote" if supported else "", "", True)


def test_provider_failure_invalidates_quality_metrics_instead_of_counting_as_model_error() -> None:
    cases = [{"case_id": "positive"}, {"case_id": "negative"}]
    result = calculate_quality_metrics(
        cases, {"positive": True, "negative": False}, [decision("positive", True)]
    )
    assert result["quality_metrics_valid"] is False
    assert result["answerable_recall"] is None
    assert result["no_answer_specificity"] is None
    assert result["observed_answerable_recall"] == 1.0
    assert result["decision_coverage"] == 0.5
    assert result["missing_decision_count"] == 1


def test_complete_provider_run_reports_quality_metrics() -> None:
    cases = [{"case_id": "positive"}, {"case_id": "negative"}]
    result = calculate_quality_metrics(
        cases,
        {"positive": True, "negative": False},
        [decision("positive", True), decision("negative", False)],
    )
    assert result["quality_metrics_valid"] is True
    assert result["answerable_recall"] == 1.0
    assert result["no_answer_specificity"] == 1.0
