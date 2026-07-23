"""Policy for choosing a predictable pipeline over an experimental Agent."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    mode: str
    reason: str
    experimental: bool


def choose_remediation_mode(requested: str, *, experimental_opt_in: bool) -> ExecutionDecision:
    if requested == "pipeline":
        return ExecutionDecision("pipeline", "deterministic_default", False)
    if requested != "agent":
        raise ValueError("unsupported_execution_mode")
    if not experimental_opt_in:
        raise ValueError("agent_requires_explicit_experimental_opt_in")
    return ExecutionDecision("agent", "explicit_experimental_opt_in", True)
