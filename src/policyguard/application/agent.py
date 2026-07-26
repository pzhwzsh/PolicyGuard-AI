"""Bounded tool-using agent core with explicit budgets and trace."""

import json
from dataclasses import asdict, dataclass, field
from typing import Protocol

from policyguard.application.guardrails import default_guardrail_policy
from policyguard.application.provider_http import (
    ProviderRetryPolicy,
    post_with_retry,
    should_try_backup,
)
from policyguard.application.security import detect_prompt_injection, redact_sensitive
from policyguard.application.tools import ToolRegistry


class AgentPlanner(Protocol):
    def next_action(self, context: dict, tools: list[dict], trace: list[dict]) -> dict: ...


@dataclass(frozen=True, slots=True)
class AgentBudget:
    max_steps: int = 4
    max_tool_calls: int = 2


@dataclass(slots=True)
class AgentOutcome:
    status: str
    result: dict = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)
    tool_calls: int = 0
    planner_calls: int = 0
    total_tokens: int = 0
    context_metadata: dict = field(default_factory=dict)


class ControlledAgent:
    def __init__(self, planner: AgentPlanner, tools: ToolRegistry, budget: AgentBudget) -> None:
        self.planner = planner
        self.tools = tools
        self.budget = budget

    def run(self, context: dict) -> AgentOutcome:
        context_metadata = dict(context.get("context_metadata", {}))
        try:
            default_guardrail_policy().validate_tools(self.tools.definitions())
        except ValueError as exc:
            return AgentOutcome(
                "manual_review", {"reason": str(exc)}, [], 0, 0, 0, context_metadata
            )
        trace = []
        tool_calls = 0
        planner_calls = 0
        total_tokens = 0
        injection_matches = detect_prompt_injection(context)
        if injection_matches:
            trace.append(
                {
                    "step": 0,
                    "type": "security_block",
                    "matches": injection_matches,
                    "context": redact_sensitive(context),
                }
            )
            return AgentOutcome(
                "manual_review", {"reason": "prompt_injection_detected"}, trace, tool_calls,
                planner_calls, total_tokens, context_metadata
            )
        for step in range(1, self.budget.max_steps + 1):
            try:
                action = self.planner.next_action(context, self.tools.definitions(), trace)
                planner_calls += 1
                usage = action.pop("_usage", {})
                total_tokens += int(usage.get("total_tokens", 0) or 0)
            except Exception as exc:
                trace.append({"step": step, "type": "planner_error", "error": type(exc).__name__})
                return AgentOutcome(
                    "manual_review", {"reason": "planner_failed"}, trace, tool_calls,
                    planner_calls, total_tokens, context_metadata
                )
            action_type = action.get("type")
            trace.append({"step": step, "type": "decision", "action": action})
            if action_type == "finish":
                return AgentOutcome(
                    "completed", action.get("result", {}), trace, tool_calls,
                    planner_calls, total_tokens, context_metadata
                )
            if action_type != "tool_call":
                return AgentOutcome(
                    "manual_review", {"reason": "invalid_agent_action"}, trace, tool_calls,
                    planner_calls, total_tokens, context_metadata
                )
            if tool_calls >= self.budget.max_tool_calls:
                return AgentOutcome(
                    "manual_review", {"reason": "tool_budget_exceeded"}, trace, tool_calls,
                    planner_calls, total_tokens, context_metadata
                )
            try:
                tool_result = self.tools.execute(action["tool"], action.get("arguments", {}))
            except Exception as exc:
                trace.append({"step": step, "type": "tool_error", "error": type(exc).__name__})
                return AgentOutcome(
                    "manual_review", {"reason": "tool_failed"}, trace, tool_calls,
                    planner_calls, total_tokens, context_metadata
                )
            tool_calls += 1
            observation = asdict(tool_result)
            trace.append({"step": step, "type": "observation", "value": observation})
            context = {**context, "last_observation": observation}
        return AgentOutcome(
            "manual_review", {"reason": "step_budget_exceeded"}, trace, tool_calls,
            planner_calls, total_tokens, context_metadata
        )


@dataclass(frozen=True, slots=True)
class OpenAICompatibleAgentPlanner:
    base_url: str
    api_key: str
    model: str
    reasoning_effort: str = "medium"
    timeout_seconds: float = 60
    retry_policy: ProviderRetryPolicy = ProviderRetryPolicy()

    @property
    def model_name(self) -> str:
        return self.model

    def next_action(self, context: dict, tools: list[dict], trace: list[dict]) -> dict:
        response = post_with_retry(
            self.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "reasoning_effort": self.reasoning_effort,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an internal compliance remediation planner. Use only listed "
                            "tools. Never claim an external write occurred. Return JSON only as "
                            '{"type":"tool_call","tool":string,"arguments":object} or '
                            '{"type":"finish","result":object}.'
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"context": context, "tools": tools, "trace": trace[-4:]},
                            ensure_ascii=False,
                        ),
                    },
                ],
            },
            timeout=self.timeout_seconds,
            policy=self.retry_policy,
        )
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            action = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("agent_response_invalid_json") from exc
        if not isinstance(action, dict) or action.get("type") not in {"tool_call", "finish"}:
            raise ValueError("agent_action_schema_invalid")
        if action["type"] == "tool_call" and not isinstance(action.get("tool"), str):
            raise ValueError("agent_tool_name_invalid")
        if action["type"] == "tool_call" and not isinstance(action.get("arguments", {}), dict):
            raise ValueError("agent_tool_arguments_invalid")
        action["_usage"] = payload.get("usage", {})
        action["_provider_attempts"] = response.extensions.get("policyguard_attempts", 1)
        return action


class FallbackAgentPlanner:
    """Try planners in order; only fallback after provider/parse failures."""

    def __init__(self, *planners: OpenAICompatibleAgentPlanner) -> None:
        self.planners = planners
        self.last_model: str | None = None

    @property
    def model_name(self) -> str:
        return "->".join(planner.model_name for planner in self.planners)

    def next_action(self, context: dict, tools: list[dict], trace: list[dict]) -> dict:
        for planner in self.planners:
            try:
                action = planner.next_action(context, tools, trace)
                self.last_model = planner.model_name
                return action
            except Exception as exc:
                if not should_try_backup(exc):
                    raise
        raise RuntimeError("all_agent_planners_failed")


def configured_agent_planner(settings) -> OpenAICompatibleAgentPlanner | None:
    if getattr(settings, "app_env", "") == "test":
        return None
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return None
    policy = ProviderRetryPolicy(
        settings.provider_max_attempts, settings.provider_backoff_seconds
    )
    models = [settings.llm_model]
    if settings.llm_fallback_model and settings.llm_fallback_model not in models:
        models.append(settings.llm_fallback_model)
    planners = tuple(
        OpenAICompatibleAgentPlanner(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=model,
            reasoning_effort=settings.llm_reasoning_effort,
            timeout_seconds=settings.llm_timeout_seconds,
            retry_policy=policy,
        )
        for model in models
    )
    return planners[0] if len(planners) == 1 else FallbackAgentPlanner(*planners)
