import json

import httpx

from policyguard.scripts.benchmark_llm import evaluate_model


def test_llm_benchmark_counts_quality_latency_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][1]["content"]
        title = user.split("title: ", 1)[1].split("\n", 1)[0]
        description = user.split("description: ", 1)[1]
        content = json.dumps(
            {
                "claims": [
                    {"text": title, "field": "title", "confidence": 1},
                    {"text": description, "field": "description", "confidence": 1},
                ]
            }
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 100}},
        )

    samples = [{"title": "claim one", "description": "claim two"}]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = evaluate_model(client, "https://example.test/v1", "secret", "candidate", samples)
    assert result["requests_ok"] == 1
    assert result["both_fields_covered"] == 1
    assert result["faithful_samples"] == 1
    assert result["total_tokens"] == 100
    assert result["cost_usd"] is None
    assert result["cost_status"] == "not_reported_missing_pricing"


def test_llm_benchmark_uses_observed_token_split_for_cost() -> None:
    content = json.dumps({"claims": [
        {"text": "a", "field": "title", "confidence": 1},
        {"text": "b", "field": "description", "confidence": 1},
    ]})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }))
    with httpx.Client(transport=transport) as client:
        result = evaluate_model(
            client, "https://example.test/v1", "secret", "candidate",
            [{"title": "a", "description": "b"}], {"input": 1.0, "output": 2.0},
        )
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50
    assert result["cost_usd"] == 0.0002


def test_llm_benchmark_records_provider_failure() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503, json={"error": "down"}))
    with httpx.Client(transport=transport) as client:
        result = evaluate_model(
            client,
            "https://example.test/v1",
            "secret",
            "candidate",
            [{"title": "a", "description": "b"}],
        )
    assert result["success_rate"] == 0
    assert result["errors"] == ["503"]
