from dataclasses import dataclass
from difflib import unified_diff
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_name: str
    success: bool
    output: dict[str, Any]


class Tool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    def execute(self, arguments: dict[str, Any]) -> ToolResult: ...


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"unknown_tool:{name}")
        return tool.execute(arguments)

    def definitions(self) -> list[dict[str, str]]:
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
        ]


class SuggestConservativeRewriteTool:
    name = "suggest_conservative_rewrite"
    description = "Create an internal rewrite plan without mutating an external product."

    # Longer forms come first so deletion does not leave a dangling Chinese particle.
    _risky_phrases = (
        "100%安全的", "最好的", "国家级", "最高级", "最佳", "最好", "100%安全"
    )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        product = arguments["product"]
        legal_basis = self._legal_basis(arguments.get("evidence", []))
        operations = []
        for field in ("title", "description"):
            before = str(product.get(field, ""))
            after = before
            removed = []
            for phrase in self._risky_phrases:
                if phrase in after:
                    after = after.replace(phrase, "")
                    removed.append(phrase)
            after = " ".join(after.split()).strip("，,。 ")
            if after != before:
                operations.append(
                    {
                        "operation": "replace_field",
                        "field": field,
                        "before": before,
                        "after": after,
                        "removed_phrases": removed,
                        "reason": "Conservative baseline removes risky claims; human review required.",
                        "claim_spans": [
                            {
                                "text": phrase,
                                "start": before.find(phrase),
                                "end": before.find(phrase) + len(phrase),
                            }
                            for phrase in removed
                        ],
                        "legal_basis": legal_basis,
                        "diff": "\n".join(unified_diff(
                            [before], [after], fromfile=f"{field}:before",
                            tofile=f"{field}:after", lineterm="",
                        )),
                        "meaning_preservation": {
                            "strategy": "minimal_lexical_deletion",
                            "unchanged_ratio": round(len(after) / max(1, len(before)), 4),
                            "requires_human_review": True,
                        },
                    }
                )
        return ToolResult(
            tool_name=self.name,
            success=True,
            output={"operations": operations, "external_side_effect": False},
        )

    @staticmethod
    def _legal_basis(markets: list[dict]) -> list[dict]:
        basis = []
        for market in markets:
            for hit in market.get("candidate_evidence", [])[:2]:
                basis.append({
                    "jurisdiction": market.get("market"),
                    "section_id": hit.get("section_id"),
                    "heading": hit.get("heading"),
                    "quote": str(hit.get("text", ""))[:1200],
                    "source_url": hit.get("source_url"),
                    "evidence_status": "human_reviewed_candidate",
                })
        return basis
