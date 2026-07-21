from policyguard.application.compliance_report import (
    build_compliance_report,
    report_markdown,
    report_pdf,
)
from policyguard.domain.workflow import WorkflowRun, WorkflowStatus


def test_report_contains_only_persisted_claims_evidence_and_sources() -> None:
    run = WorkflowRun(
        id="run-1",
        status=WorkflowStatus.REVIEW_REQUIRED,
        current_step="human_review_route",
        input_payload={
            "product": {"title": "Claim"}, "markets": ["US"],
            "category": "all", "channel": "all", "as_of": None,
        },
        result_payload={
            "claims": [{"text": "Claim"}],
            "markets": [{
                "market": "US",
                "candidate_evidence": [{
                    "section_id": "s1", "heading": "Truthfulness",
                    "text": "Advertising must be truthful.",
                    "source_url": "https://official.test/rule", "score": 0.9,
                }],
            }],
        },
    )
    report = build_compliance_report(run)
    markdown = report_markdown(report)
    assert report["evidence_only"] is True
    assert report["markets"][0]["evidence"][0]["quote"] == "Advertising must be truthful."
    assert "https://official.test/rule" in markdown
    assert "Candidate evidence is not a legal opinion." in markdown
    assert report_pdf(report).startswith(b"%PDF")
