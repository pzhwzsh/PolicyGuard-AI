"""Bounded multi-agent DAG with structured messages and shared budgets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentNode:
    agent_id: str
    role: str
    task: str
    depends_on: tuple[str, ...] = ()


class MultiAgentCoordinator:
    def __init__(self, max_agents: int = 4, max_messages: int = 24, max_tokens: int = 24_000):
        self.max_agents = max_agents
        self.max_messages = max_messages
        self.max_tokens = max_tokens

    def execute(
        self,
        nodes: list[AgentNode],
        runner: Callable[[AgentNode, list[dict[str, Any]], int], dict[str, Any]],
    ) -> dict[str, Any]:
        self._validate(nodes)
        pending = {node.agent_id: node for node in nodes}
        outputs: dict[str, dict[str, Any]] = {}
        messages: list[dict[str, Any]] = []
        used_tokens = 0
        while pending:
            ready = sorted(
                (node for node in pending.values() if set(node.depends_on) <= outputs.keys()),
                key=lambda node: node.agent_id,
            )
            if not ready:
                raise ValueError("multi_agent_dependency_cycle")
            for node in ready:
                inputs = [
                    {"from": dependency, "to": node.agent_id, "payload": outputs[dependency]}
                    for dependency in node.depends_on
                ]
                if len(messages) + len(inputs) > self.max_messages:
                    raise RuntimeError("multi_agent_message_budget_exceeded")
                remaining = self.max_tokens - used_tokens
                if remaining <= 0:
                    raise RuntimeError("multi_agent_token_budget_exceeded")
                result = runner(node, inputs, remaining)
                tokens = max(int(result.get("tokens", 0)), 0)
                if tokens > remaining:
                    raise RuntimeError("multi_agent_token_budget_exceeded")
                used_tokens += tokens
                outputs[node.agent_id] = result
                messages.extend(inputs)
                messages.append({
                    "from": node.agent_id,
                    "to": "coordinator",
                    "payload": result,
                    "schema": "policyguard.agent-message.v1",
                })
                pending.pop(node.agent_id)
        return {
            "status": "completed",
            "outputs": outputs,
            "messages": messages,
            "usage": {"agents": len(nodes), "messages": len(messages), "tokens": used_tokens},
        }

    def _validate(self, nodes: list[AgentNode]) -> None:
        if not 1 <= len(nodes) <= self.max_agents:
            raise ValueError("multi_agent_count_out_of_range")
        identifiers = {node.agent_id for node in nodes}
        if len(identifiers) != len(nodes):
            raise ValueError("multi_agent_id_collision")
        if any(not set(node.depends_on) <= identifiers - {node.agent_id} for node in nodes):
            raise ValueError("multi_agent_dependency_invalid")
