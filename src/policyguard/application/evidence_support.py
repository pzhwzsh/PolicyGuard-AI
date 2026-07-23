"""LLM evidence-support verification with exact-quote enforcement."""

import json
import re
from dataclasses import dataclass

from policyguard.application.provider_http import (
    ProviderRetryPolicy,
    post_with_retry,
    should_try_backup,
)


class EvidenceVerifier:
    def verify_batch(self, cases: list[dict]) -> tuple[list["EvidenceDecision"], dict]: ...


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    case_id: str
    supported: bool
    quote: str
    reason: str
    quote_valid: bool


@dataclass(frozen=True, slots=True)
class OpenAICompatibleEvidenceVerifier:
    base_url: str
    api_key: str
    model: str
    reasoning_effort: str = "medium"
    timeout_seconds: float = 120
    retry_policy: ProviderRetryPolicy = ProviderRetryPolicy()

    def verify_batch(self, cases: list[dict]) -> tuple[list[EvidenceDecision], dict]:
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
                            "Decide whether the supplied evidence directly answers each query. "
                            "Topical similarity is not enough. If supported, quote an exact, "
                            "contiguous substring from evidence. If the evidence lacks the "
                            "specific rule requested, set supported=false and quote=''. Return "
                            'JSON only: {"decisions":[{"case_id":string,'
                            '"supported":boolean,"quote":string,"reason":string}]}.'
                        ),
                    },
                    {"role": "user", "content": json.dumps(cases, ensure_ascii=False)},
                ],
            },
            timeout=self.timeout_seconds,
            policy=self.retry_policy,
        )
        payload = response.json()
        decisions = self.parse(payload["choices"][0]["message"].get("content", ""), cases)
        return decisions, payload.get("usage", {})

    @staticmethod
    def parse(content: str, cases: list[dict]) -> list[EvidenceDecision]:
        cleaned = content.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
        if fenced:
            cleaned = fenced.group(1)
        payload = json.loads(cleaned)
        raw = payload.get("decisions")
        if not isinstance(raw, list):
            raise ValueError("evidence_decisions_must_be_a_list")
        evidence_by_id = {item["case_id"]: item["evidence"] for item in cases}
        decisions = []
        for item in raw:
            case_id = str(item.get("case_id", ""))
            if case_id not in evidence_by_id:
                raise ValueError("evidence_decision_unknown_case")
            supported = bool(item.get("supported", False))
            quote = str(item.get("quote", "")).strip()
            quote_valid = (
                bool(quote and quote in evidence_by_id[case_id]) if supported else not quote
            )
            decisions.append(
                EvidenceDecision(
                    case_id=case_id,
                    supported=supported and quote_valid,
                    quote=quote,
                    reason=str(item.get("reason", "")),
                    quote_valid=quote_valid,
                )
            )
        return decisions


class FallbackEvidenceVerifier:
    def __init__(self, *providers: EvidenceVerifier) -> None:
        self.providers = providers

    def verify_batch(self, cases: list[dict]) -> tuple[list[EvidenceDecision], dict]:
        for index, provider in enumerate(self.providers):
            try:
                decisions, usage = provider.verify_batch(cases)
                return decisions, {
                    **usage,
                    "selected_model": getattr(provider, "model", "unknown"),
                    "fallback_used": index > 0,
                }
            except Exception as exc:
                if not should_try_backup(exc):
                    raise
                continue
        raise RuntimeError("all_evidence_verifiers_failed")


def configured_evidence_verifier(settings) -> EvidenceVerifier | None:
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
    providers = tuple(
        OpenAICompatibleEvidenceVerifier(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=model,
            reasoning_effort=settings.llm_reasoning_effort,
            timeout_seconds=settings.llm_timeout_seconds,
            retry_policy=policy,
        )
        for model in models
    )
    return providers[0] if len(providers) == 1 else FallbackEvidenceVerifier(*providers)
