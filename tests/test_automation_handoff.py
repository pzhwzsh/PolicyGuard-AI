from policyguard.application.automation_handoff import (
    build_dingtalk_preview,
    build_rpa_handoff,
)
from policyguard.domain.workflow import WorkflowRun, WorkflowStatus


def test_automation_handoffs_are_preview_only() -> None:
    run = WorkflowRun(
        id="run-1",
        status=WorkflowStatus.DRAFT_READY,
        current_step="draft",
        input_payload={},
        result_payload={
            "draft": {"operations": [{"field": "title", "after": "safe"}]},
            "markets": [{"candidate_evidence": [{"section_id": "a1"}]}],
        },
    )
    rpa = build_rpa_handoff(run)
    dingtalk = build_dingtalk_preview(run, "/report")
    assert rpa["automatic_execution"] is False
    assert dingtalk["automatic_send"] is False
    assert "候选证据：1" in dingtalk["markdown"]["text"]
