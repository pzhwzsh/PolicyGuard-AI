from policyguard.application.agent import AgentBudget, ControlledAgent
from policyguard.application.tools import SuggestConservativeRewriteTool, ToolRegistry


class RewriteThenFinishPlanner:
    def next_action(self, context, tools, trace):
        if "last_observation" not in context:
            return {
                "type": "tool_call",
                "tool": "suggest_conservative_rewrite",
                "arguments": {"product": context["product"]},
            }
        return {"type": "finish", "result": context["last_observation"]["output"]}


class EndlessPlanner:
    def next_action(self, context, tools, trace):
        return {
            "type": "tool_call",
            "tool": "suggest_conservative_rewrite",
            "arguments": {"product": context["product"]},
        }


def test_agent_executes_allowlisted_tool_and_records_trace() -> None:
    agent = ControlledAgent(
        RewriteThenFinishPlanner(),
        ToolRegistry([SuggestConservativeRewriteTool()]),
        AgentBudget(max_steps=3, max_tool_calls=1),
    )
    outcome = agent.run({"product": {"title": "国家级产品", "description": ""}})
    assert outcome.status == "completed"
    assert outcome.tool_calls == 1
    assert outcome.result["external_side_effect"] is False
    assert any(item["type"] == "observation" for item in outcome.trace)


def test_agent_stops_when_tool_budget_is_exceeded() -> None:
    agent = ControlledAgent(
        EndlessPlanner(),
        ToolRegistry([SuggestConservativeRewriteTool()]),
        AgentBudget(max_steps=4, max_tool_calls=1),
    )
    outcome = agent.run({"product": {"title": "国家级产品", "description": ""}})
    assert outcome.status == "manual_review"
    assert outcome.result["reason"] == "tool_budget_exceeded"
