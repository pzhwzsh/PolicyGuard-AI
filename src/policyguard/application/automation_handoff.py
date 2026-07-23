"""Side-effect-free handoff payloads for RPA and DingTalk integrations."""

from hashlib import sha256

from policyguard.domain.workflow import WorkflowRun, WorkflowStatus


def build_rpa_handoff(run: WorkflowRun) -> dict:
    if run.status != WorkflowStatus.DRAFT_READY:
        raise RuntimeError("workflow_draft_required")
    draft = run.result_payload.get("draft", {})
    operations = draft.get("operations") or run.result_payload.get(
        "remediation_plan", {}
    ).get("operations", [])
    return {
        "schema": "policyguard.rpa-handoff.v1",
        "workflow_id": run.id,
        "idempotency_key": sha256(f"rpa:{run.id}:{len(operations)}".encode()).hexdigest(),
        "mode": "reviewed_draft_only",
        "operations": operations,
        "preconditions": [
            "operator confirms target system and account",
            "operator previews field-level changes",
            "RPA records external receipt without automatic retry on validation errors",
        ],
        "automatic_execution": False,
    }


def build_dingtalk_preview(run: WorkflowRun, report_url: str) -> dict:
    markets = run.result_payload.get("markets", [])
    evidence_count = sum(len(item.get("candidate_evidence", [])) for item in markets)
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": f"PolicyGuard 审查 {run.id[:8]}",
            "text": (
                f"### PolicyGuard 审查待处理\n\n"
                f"- 工作流：`{run.id}`\n"
                f"- 状态：**{run.status.value}**\n"
                f"- 候选证据：{evidence_count}\n"
                f"- [查看可审计报告]({report_url})\n\n"
                "> 此消息是发送预览，不会自动调用钉钉 webhook。"
            ),
        },
        "at": {"isAtAll": False},
        "automatic_send": False,
    }
