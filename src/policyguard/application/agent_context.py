"""PolicyGuard-specific context assembly with explicit priority and size budgets."""

import json
from dataclasses import dataclass
from typing import Any

from policyguard.application.ports import AgentMemoryRepository
from policyguard.domain.agent_memory import MemoryQuery


def _estimate_tokens(value: Any) -> int:
    # Conservative local estimate for mixed Chinese/English JSON; provider usage remains canonical.
    return max(1, (len(json.dumps(value, ensure_ascii=False)) + 2) // 3)


def _clip(value: str, characters: int) -> str:
    return value if len(value) <= characters else value[:characters].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class AgentContextBudget:
    max_context_tokens: int = 6000
    evidence_tokens: int = 3600
    memory_tokens: int = 900
    max_memories: int = 3


class AgentContextBuilder:
    """Builds auditable context; current law always outranks historical experience."""

    def __init__(
        self,
        memory_repository: AgentMemoryRepository | None = None,
        budget: AgentContextBudget | None = None,
    ) -> None:
        self.memories = memory_repository
        self.budget = budget or AgentContextBudget()

    def build_remediation_context(self, run) -> dict[str, Any]:
        payload = run.input_payload
        product = dict(payload["product"])
        product["title"] = _clip(str(product.get("title", "")), 1200)
        product["description"] = _clip(str(product.get("description", "")), 6000)
        evidence = self._bounded_evidence(run.result_payload.get("markets", []))
        recalled = []
        if self.memories is not None:
            query = MemoryQuery(
                task_type="remediation",
                jurisdictions=tuple(str(item).upper() for item in payload.get("markets", [])),
                category=str(payload.get("category", "all")),
                channel=str(payload.get("channel", "all")),
                query_text=f"{product.get('title', '')} {product.get('description', '')}",
                limit=self.budget.max_memories,
            )
            recalled = [
                {
                    "memory_id": item.id,
                    "source_run_id": item.run_id,
                    "jurisdictions": list(item.jurisdictions),
                    "category": item.category,
                    "summary": item.summary,
                    "outcome": item.outcome,
                    "review_status": item.review_status,
                    "reviewed_by": item.reviewed_by,
                    "source_versions": item.source_versions,
                }
                for item in self.memories.recall(query)
            ]
            while recalled and _estimate_tokens(recalled) > self.budget.memory_tokens:
                recalled.pop()
        context = {
            "product": product,
            "evidence": evidence,
            "reviewed_case_memory": recalled,
            "instruction": (
                "Create a conservative internal remediation plan. Current cited legal evidence "
                "has higher authority than historical cases; memories are non-authoritative examples."
            ),
        }
        while evidence and _estimate_tokens(context) > self.budget.max_context_tokens:
            evidence.pop()
        context["context_metadata"] = {
            "policy": "current_evidence_over_reviewed_memory",
            "estimated_tokens": _estimate_tokens(context),
            "evidence_market_count": len(evidence),
            "recalled_memory_ids": [item["memory_id"] for item in recalled],
            "memory_count": len(recalled),
            "budget_tokens": self.budget.max_context_tokens,
        }
        return context

    def _bounded_evidence(self, markets: list[dict]) -> list[dict]:
        bounded: list[dict] = []
        for market in markets:
            hits = []
            for hit in market.get("candidate_evidence", [])[:3]:
                hits.append({
                    "section_id": hit.get("section_id"),
                    "heading": _clip(str(hit.get("heading", "")), 500),
                    "text": _clip(str(hit.get("text", "")), 3600),
                    "source_url": hit.get("source_url"),
                })
            bounded.append({"market": market.get("market"), "candidate_evidence": hits})
        while bounded and _estimate_tokens(bounded) > self.budget.evidence_tokens:
            largest = max(bounded, key=lambda item: len(item["candidate_evidence"]))
            if largest["candidate_evidence"]:
                largest["candidate_evidence"].pop()
            else:
                bounded.pop()
        return bounded
