"""Run a reproducible structured-claim benchmark against an OpenAI-compatible API."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from statistics import mean

import httpx
from dotenv import load_dotenv

SYSTEM_PROMPT = (
    "Extract every independently verifiable marketing claim from title and description. "
    "Preserve each claim verbatim. Include at least one item for each non-empty field. "
    'Do not merge fields. Return JSON only: {"claims":[{"text":string,'
    '"field":"title"|"description","confidence":number}]}'
)
DEFAULT_MODELS = ["gpt-5.6", "gpt-5.6-terra", "gpt-5.6-sol"]


def evaluate_model(
    client: httpx.Client,
    base_url: str,
    key: str,
    model: str,
    samples: list[dict],
    pricing: dict | None = None,
) -> dict:
    latencies: list[float] = []
    input_usages: list[int] = []
    output_usages: list[int] = []
    success = valid = covered = faithful = 0
    errors: list[str] = []
    for sample in samples:
        started = time.perf_counter()
        try:
            response = client.post(
                base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "temperature": 0,
                    "reasoning_effort": "medium",
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"title: {sample['title']}\n"
                                f"description: {sample['description']}"
                            ),
                        },
                    ],
                },
            )
            latencies.append((time.perf_counter() - started) * 1000)
            if response.status_code != 200:
                errors.append(str(response.status_code))
                continue
            success += 1
            payload = response.json()
            usage = payload.get("usage", {})
            input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
            output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
            if input_tokens is None and output_tokens is None:
                input_tokens = usage.get("total_tokens")
                output_tokens = 0
            if isinstance(input_tokens, int):
                input_usages.append(input_tokens)
            if isinstance(output_tokens, int):
                output_usages.append(output_tokens)
            try:
                claims = json.loads(payload["choices"][0]["message"].get("content", "")).get(
                    "claims", []
                )
                if not isinstance(claims, list):
                    raise ValueError("claims_not_list")
                valid += 1
                fields = {item.get("field") for item in claims if isinstance(item, dict)}
                covered += int({"title", "description"} <= fields)
                faithful += int(
                    all(
                        isinstance(item, dict)
                        and item.get("text", "")
                        in (
                            sample["title"]
                            if item.get("field") == "title"
                            else sample["description"]
                        )
                        for item in claims
                    )
                )
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                errors.append("invalid_json")
        except Exception as exc:  # network/provider failures are benchmark data
            errors.append(type(exc).__name__)
    total_input = sum(input_usages)
    total_output = sum(output_usages)
    price = pricing or {}
    observed_cost = None
    if price.get("input") is not None and price.get("output") is not None:
        observed_cost = round(
            (total_input * float(price["input"]) + total_output * float(price["output"]))
            / 1_000_000,
            8,
        )
    ordered = sorted(latencies)

    def percentile(value: float) -> float | None:
        if not ordered:
            return None
        index = min(len(ordered) - 1, round((len(ordered) - 1) * value))
        return round(ordered[index], 1)

    sample_count = len(samples)
    return {
        "model": model,
        "sample_count": len(samples),
        "requests_ok": success,
        "json_valid": valid,
        "both_fields_covered": covered,
        "faithful_samples": faithful,
        "success_rate": success / sample_count if sample_count else 0,
        "json_valid_rate": valid / sample_count if sample_count else 0,
        "field_coverage_rate": covered / sample_count if sample_count else 0,
        "faithfulness_rate": faithful / sample_count if sample_count else 0,
        "mean_latency_ms": round(mean(latencies), 1) if latencies else None,
        "p50_latency_ms": percentile(0.50),
        "p95_latency_ms": percentile(0.95),
        "p99_latency_ms": percentile(0.99),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "mean_tokens": round((total_input + total_output) / sample_count, 1)
        if sample_count
        else None,
        "cost_usd": observed_cost,
        "pricing_usd_per_million_tokens": price or None,
        "cost_status": "measured_from_provider_usage"
        if observed_cost is not None
        else "not_reported_missing_pricing",
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--dataset", default="data/evaluation/llm-claims.json")
    parser.add_argument("--output", default="data/benchmarks/llm-results.json")
    parser.add_argument("--matrix", default="config/model_matrix.json")
    args = parser.parse_args()
    load_dotenv()
    base_url, key = os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_API_KEY", "")
    if not base_url or not key:
        print("SKIPPED: configure LLM_BASE_URL and LLM_API_KEY first")
        return
    root = Path(__file__).parents[3]
    dataset = json.loads((root / args.dataset).read_text(encoding="utf-8"))
    matrix = json.loads((root / args.matrix).read_text(encoding="utf-8"))
    pricing = {
        item["model"]: item.get("pricing_usd_per_million_tokens") for item in matrix.get("llm", [])
    }
    with httpx.Client(timeout=120) as client:
        results = [
            evaluate_model(client, base_url, key, model, dataset["samples"], pricing.get(model))
            for model in args.models
        ]
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print(
            f"{result['model']}: {result['success_rate']:.2f} success, "
            f"{result['mean_latency_ms']} ms, {result['mean_tokens']} tokens"
        )
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
