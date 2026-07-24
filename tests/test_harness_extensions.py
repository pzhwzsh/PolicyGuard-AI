from pathlib import Path

import httpx
import pytest

from policyguard.application import harness_mcp
from policyguard.application.harness_evaluation import evaluate_harness
from policyguard.application.harness_mcp import MCPClientRegistry, MCPServerConfig
from policyguard.application.harness_multi_agent import AgentNode, MultiAgentCoordinator
from policyguard.application.harness_sandbox import SandboxPolicy, docker_command
from policyguard.application.harness_skills import SkillRegistry


def test_skill_discovery_and_permission_declaration() -> None:
    root = Path(__file__).parents[1] / "config" / "skills"
    registry = SkillRegistry(
        root, {"inspect_context", "policy_preflight", "sandbox_python"}
    )
    skills, failures = registry.discover()
    assert failures == []
    assert {skill.name for skill in skills} == {"compliance_review", "sandbox_analysis"}
    assert registry.get("sandbox_analysis").permissions == (
        "context:read", "sandbox:execute"
    )


def test_docker_command_isolated_by_default(tmp_path: Path) -> None:
    command = docker_command(tmp_path, SandboxPolicy())
    rendered = " ".join(command)
    assert "--network=none" in rendered
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "no-new-privileges" in rendered
    assert "readonly" in rendered


def test_mcp_client_limits_collisions_and_result(monkeypatch) -> None:
    config = MCPServerConfig(
        "official", "https://mcp.example.com/mcp", allowed_hosts=("mcp.example.com",)
    )
    registry = MCPClientRegistry([config])
    registry.register_tools("official", [{"name": "search"}])
    other = MCPClientRegistry([
        config,
        MCPServerConfig("other", "https://other.example.com/mcp"),
    ])
    other.register_tools("official", [{"name": "search"}])
    with pytest.raises(ValueError, match="mcp_tool_collision"):
        other.register_tools("other", [{"name": "search"}])

    response = httpx.Response(
        200, json={"jsonrpc": "2.0", "id": "1", "result": {"ok": True}}
    )
    response.extensions["policyguard_attempts"] = 2
    monkeypatch.setattr(harness_mcp, "validate_outbound_url", lambda *args: None)
    monkeypatch.setattr(harness_mcp, "post_with_retry", lambda *args, **kwargs: response)
    result = registry.call_tool("official", "search", {"q": "policy"})
    assert result["result"] == {"ok": True}
    assert result["provider_attempts"] == 2


def test_multi_agent_dag_and_shared_budget() -> None:
    nodes = [
        AgentNode("research", "researcher", "Find evidence"),
        AgentNode("verify", "verifier", "Verify evidence", ("research",)),
    ]
    result = MultiAgentCoordinator(max_tokens=100).execute(
        nodes,
        lambda node, messages, remaining: {
            "agent": node.agent_id, "received": len(messages), "tokens": 10
        },
    )
    assert result["usage"] == {"agents": 2, "messages": 3, "tokens": 20}
    assert result["outputs"]["verify"]["received"] == 1
    with pytest.raises(ValueError, match="multi_agent_dependency_cycle"):
        MultiAgentCoordinator().execute(
            [
                AgentNode("a", "one", "task", ("b",)),
                AgentNode("b", "two", "task", ("a",)),
            ],
            lambda *args: {},
        )


def test_harness_evaluation_reports_tool_and_recovery_metrics() -> None:
    report = evaluate_harness(
        [{"case_id": "a", "expected_tools": ["inspect"], "expected_status": "completed"}],
        lambda case: {
            "status": "completed",
            "usage": {"tokens": 20, "cost_microusd": 2},
            "events": [
                {"type": "tool.completed", "detail": {"tool": "inspect"}},
                {"type": "checkpoint.saved", "detail": {}},
            ],
        },
    )
    assert report["metrics"]["task_success_rate"] == 1
    assert report["metrics"]["mean_tool_precision"] == 1
    assert report["metrics"]["recovery_signal_rate"] == 1
