from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256

from policyguard.application.agent import ControlledAgent
from policyguard.application.agent_context import AgentContextBuilder
from policyguard.application.ports import AgentMemoryRepository, WorkflowRepository
from policyguard.application.tools import ToolRegistry
from policyguard.domain.workflow import WorkflowEvent, WorkflowRun, WorkflowStatus
from policyguard.domain.agent_memory import AgentMemory


class RemediationService:
    def __init__(
        self, repository: WorkflowRepository, tools: ToolRegistry,
        context_builder: AgentContextBuilder | None = None,
        memory_repository: AgentMemoryRepository | None = None,
    ) -> None:
        self.repository = repository
        self.tools = tools
        self.context_builder = context_builder or AgentContextBuilder()
        self.memory_repository = memory_repository

    def plan(self, *, run_id: str, plan_id: str) -> WorkflowRun:
        run = self._required_run(run_id)
        existing = run.result_payload.get("remediation_plan")
        if isinstance(existing, dict) and existing.get("plan_id") == plan_id:
            return run
        if run.status != WorkflowStatus.REVIEW_ACCEPTED:
            raise RuntimeError("workflow_not_ready_for_remediation")
        tool_result = self.tools.execute(
            "suggest_conservative_rewrite", {"product": run.input_payload["product"]}
        )
        run.result_payload["remediation_plan"] = {
            "plan_id": plan_id,
            "tool": tool_result.tool_name,
            "operations": tool_result.output["operations"],
            "external_side_effect": False,
            "created_at": datetime.now(UTC).isoformat(),
        }
        run.status = WorkflowStatus.REMEDIATION_PLANNED
        self._event(
            run,
            "remediation_plan_created",
            "completed",
            {"plan_id": plan_id, "operation_count": len(tool_result.output["operations"])},
        )
        return self.repository.save(run)

    def plan_with_agent(
        self, *, run_id: str, plan_id: str, agent: ControlledAgent
    ) -> WorkflowRun:
        run = self._required_run(run_id)
        existing = run.result_payload.get("remediation_plan")
        if isinstance(existing, dict) and existing.get("plan_id") == plan_id:
            return run
        if run.status != WorkflowStatus.REVIEW_ACCEPTED:
            raise RuntimeError("workflow_not_ready_for_remediation")
        outcome = agent.run(self.context_builder.build_remediation_context(run))
        run.result_payload["agent_run"] = asdict(outcome)
        if outcome.status != "completed":
            self._event(run, "agent_remediation", "manual_review", {
                "reason": outcome.result.get("reason"),
                "planner_calls": outcome.planner_calls,
                "tool_calls": outcome.tool_calls,
                "total_tokens": outcome.total_tokens,
            })
            return self.repository.save(run)
        run.result_payload["remediation_plan"] = {
            "plan_id": plan_id,
            "mode": "agent",
            "tool": "suggest_conservative_rewrite",
            "operations": outcome.result.get("operations", []),
            "external_side_effect": False,
            "created_at": datetime.now(UTC).isoformat(),
        }
        run.status = WorkflowStatus.REMEDIATION_PLANNED
        self._event(run, "agent_remediation", "completed", {
            "plan_id": plan_id,
            "planner_calls": outcome.planner_calls,
            "tool_calls": outcome.tool_calls,
            "total_tokens": outcome.total_tokens,
            "trace_length": len(outcome.trace),
        })
        return self.repository.save(run)

    def create_draft(
        self, *, run_id: str, execution_id: str, approved_by: str
    ) -> WorkflowRun:
        run = self._required_run(run_id)
        existing = run.result_payload.get("draft")
        if isinstance(existing, dict) and existing.get("execution_id") == execution_id:
            return run
        if run.status == WorkflowStatus.DRAFT_READY:
            raise RuntimeError("remediation_execution_conflict")
        if run.status != WorkflowStatus.REMEDIATION_PLANNED:
            raise RuntimeError("workflow_not_ready_for_draft")
        product = deepcopy(run.input_payload["product"])
        plan = run.result_payload["remediation_plan"]
        for operation in plan["operations"]:
            if operation["operation"] == "replace_field":
                product[operation["field"]] = operation["after"]
        run.result_payload["draft"] = {
            "execution_id": execution_id,
            "approved_by": approved_by,
            "product": product,
            "external_side_effect": False,
            "created_at": datetime.now(UTC).isoformat(),
        }
        run.status = WorkflowStatus.DRAFT_READY
        self._event(
            run,
            "internal_draft_created",
            "completed",
            {"execution_id": execution_id, "approved_by": approved_by},
        )
        if self.memory_repository is not None:
            self._store_reviewed_memory(run, approved_by)
        return self.repository.save(run)

    def _store_reviewed_memory(self, run: WorkflowRun, reviewer: str) -> None:
        urls = sorted({
            str(hit.get("source_url"))
            for market in run.result_payload.get("markets", [])
            for hit in market.get("candidate_evidence", [])
            if hit.get("source_url")
        })
        product = run.input_payload["product"]
        plan = run.result_payload["remediation_plan"]
        summary = (
            f"Reviewed remediation for category={run.input_payload.get('category', 'all')}; "
            f"title={str(product.get('title', ''))[:240]}; "
            f"operations={len(plan.get('operations', []))}."
        )
        memory = AgentMemory(
            id=sha256(f"reviewed-remediation|{run.id}".encode()).hexdigest(),
            run_id=run.id,
            task_type="remediation",
            jurisdictions=tuple(
                str(item).upper() for item in run.input_payload.get("markets", [])
            ),
            category=str(run.input_payload.get("category", "all")),
            channel=str(run.input_payload.get("channel", "all")),
            summary=summary,
            outcome={
                "operations": plan.get("operations", []),
                "external_side_effect": False,
            },
            source_versions=self.memory_repository.active_source_versions(urls),
            reviewed_by=reviewer,
        )
        self.memory_repository.save_confirmed(memory)
        self._event(run, "reviewed_case_memory_written", "completed", {
            "memory_id": memory.id,
            "reviewed_by": reviewer,
            "source_version_count": len(memory.source_versions),
        })

    def _required_run(self, run_id: str) -> WorkflowRun:
        run = self.repository.get(run_id)
        if run is None:
            raise LookupError("workflow_not_found")
        return run

    @staticmethod
    def _event(run: WorkflowRun, step: str, status: str, detail: dict) -> None:
        now = datetime.now(UTC)
        run.current_step = step
        run.updated_at = now
        run.events.append(
            WorkflowEvent(
                sequence=len(run.events) + 1,
                step=step,
                status=status,
                detail=detail,
                created_at=now,
            )
        )
