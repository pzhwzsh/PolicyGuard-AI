"""Small deterministic budgets for optional model steps."""

from dataclasses import dataclass

from policyguard.application.benchmark import estimate_tokens


@dataclass(slots=True)
class WorkflowModelBudget:
    max_calls: int = 3
    max_estimated_input_tokens: int = 12_000
    calls: int = 0
    estimated_input_tokens: int = 0

    def reserve(self, text: str) -> bool:
        tokens = estimate_tokens(text)
        if self.calls + 1 > self.max_calls:
            return False
        if self.estimated_input_tokens + tokens > self.max_estimated_input_tokens:
            return False
        self.calls += 1
        self.estimated_input_tokens += tokens
        return True

    def snapshot(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "estimated_input_tokens": self.estimated_input_tokens,
            "max_estimated_input_tokens": self.max_estimated_input_tokens,
        }
