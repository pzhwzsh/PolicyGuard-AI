from dataclasses import dataclass
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

    _risky_phrases = ("国家级", "最高级", "最佳", "最好", "100%安全")

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        product = arguments["product"]
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
                    }
                )
        return ToolResult(
            tool_name=self.name,
            success=True,
            output={"operations": operations, "external_side_effect": False},
        )
