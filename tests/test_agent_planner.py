import json

import httpx
import pytest

from policyguard.application import agent as agent_module
from policyguard.application.agent import (
    FallbackAgentPlanner,
    OpenAICompatibleAgentPlanner,
)
from policyguard.application.provider_http import ProviderRetryPolicy


def _response(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 7}},
        request=httpx.Request("POST", "https://model"),
    )


def test_agent_planner_validates_structured_action(monkeypatch) -> None:
    response = _response(json.dumps({"type": "finish", "result": {"ok": True}}))
    response.extensions["policyguard_attempts"] = 2
    monkeypatch.setattr(agent_module, "post_with_retry", lambda *args, **kwargs: response)
    planner = OpenAICompatibleAgentPlanner(
        "https://model", "key", "primary", retry_policy=ProviderRetryPolicy(max_attempts=2)
    )

    action = planner.next_action({}, [], [])

    assert action["type"] == "finish"
    assert action["_usage"]["total_tokens"] == 7
    assert action["_provider_attempts"] == 2


def test_agent_planner_rejects_invalid_json(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_module, "post_with_retry", lambda *args, **kwargs: _response("not-json")
    )
    planner = OpenAICompatibleAgentPlanner("https://model", "key", "primary")

    with pytest.raises(ValueError, match="agent_response_invalid_json"):
        planner.next_action({}, [], [])


def test_fallback_agent_planner_uses_backup_after_parse_failure(monkeypatch) -> None:
    responses = iter([
        _response("not-json"),
        _response(json.dumps({"type": "finish", "result": {}})),
    ])
    monkeypatch.setattr(agent_module, "post_with_retry", lambda *args, **kwargs: next(responses))
    planner = FallbackAgentPlanner(
        OpenAICompatibleAgentPlanner("https://model", "key", "primary"),
        OpenAICompatibleAgentPlanner("https://model", "key", "backup"),
    )

    action = planner.next_action({}, [], [])

    assert action["type"] == "finish"
    assert planner.last_model == "backup"


def test_fallback_agent_planner_does_not_mask_auth_failure(monkeypatch) -> None:
    request = httpx.Request("POST", "https://model")
    error = httpx.HTTPStatusError(
        "unauthorized", request=request, response=httpx.Response(401, request=request)
    )
    monkeypatch.setattr(
        agent_module, "post_with_retry", lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )
    planner = FallbackAgentPlanner(
        OpenAICompatibleAgentPlanner("https://model", "key", "primary"),
        OpenAICompatibleAgentPlanner("https://model", "key", "backup"),
    )

    with pytest.raises(httpx.HTTPStatusError):
        planner.next_action({}, [], [])
