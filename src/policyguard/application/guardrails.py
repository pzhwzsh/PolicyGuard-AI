"""Machine-enforced product boundaries shared by workflow and Agent paths."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROTECTED_FACT = re.compile(
    r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?\s*(?:%|ml|mL|l|L|g|kg|mg|cm|mm|元|美元|欧元)?(?![A-Za-z0-9_])"
)


def _protected_facts(operation: dict[str, Any]) -> set[str]:
    """Return numeric facts outside the claim spans intentionally removed."""
    source = str(operation.get("before", ""))
    for phrase in operation.get("removed_phrases", []):
        if isinstance(phrase, str) and phrase:
            source = source.replace(phrase, "")
    return set(_PROTECTED_FACT.findall(source))


@dataclass(frozen=True, slots=True)
class GuardrailPolicy:
    version: str
    supported_jurisdictions: tuple[str, ...]
    allowed_agent_tools: tuple[str, ...]
    allowed_remediation_fields: tuple[str, ...]
    require_legal_basis_for_rewrite: bool
    require_human_review_for_rewrite: bool
    allow_external_side_effects: bool
    allow_automatic_legal_conclusion: bool
    max_recalled_memories: int
    max_agent_steps: int
    max_agent_tool_calls: int

    @classmethod
    def load(cls, path: Path | None = None) -> "GuardrailPolicy":
        target = path or Path(__file__).parents[3] / "config" / "guardrails.json"
        payload = json.loads(target.read_text(encoding="utf-8"))
        return cls(
            **{
                **payload,
                "supported_jurisdictions": tuple(payload["supported_jurisdictions"]),
                "allowed_agent_tools": tuple(payload["allowed_agent_tools"]),
                "allowed_remediation_fields": tuple(payload["allowed_remediation_fields"]),
            }
        )

    def validate_jurisdictions(self, jurisdictions: list[str]) -> None:
        unsupported = sorted(
            {item.upper() for item in jurisdictions} - set(self.supported_jurisdictions)
        )
        if unsupported:
            raise ValueError(f"unsupported_jurisdictions:{','.join(unsupported)}")

    def validate_tools(self, definitions: list[dict]) -> None:
        denied = [
            item.get("name", "") for item in definitions
            if item.get("name") not in self.allowed_agent_tools
        ]
        if denied:
            raise ValueError(f"agent_tool_not_allowed:{','.join(denied)}")

    def validate_remediation_plan(self, output: dict[str, Any], product: dict[str, Any]) -> None:
        if output.get("external_side_effect") is not False and not self.allow_external_side_effects:
            raise ValueError("remediation_external_side_effect_forbidden")
        for operation in output.get("operations", []):
            field = operation.get("field")
            if operation.get("operation") != "replace_field":
                raise ValueError("remediation_operation_not_allowed")
            if field not in self.allowed_remediation_fields:
                raise ValueError("remediation_field_not_allowed")
            before = str(operation.get("before", ""))
            after = str(operation.get("after", ""))
            if before != str(product.get(field, "")):
                raise ValueError("remediation_stale_source_text")
            missing_facts = sorted(
                _protected_facts(operation) - set(_PROTECTED_FACT.findall(after))
            )
            if missing_facts:
                raise ValueError(f"remediation_protected_fact_removed:{','.join(missing_facts)}")
            if self.require_legal_basis_for_rewrite and not operation.get("legal_basis"):
                raise ValueError("remediation_legal_basis_required")
            if self.require_human_review_for_rewrite:
                preservation = operation.get("meaning_preservation", {})
                if preservation.get("requires_human_review") is not True:
                    raise ValueError("remediation_human_review_required")


def default_guardrail_policy() -> GuardrailPolicy:
    return GuardrailPolicy.load()
