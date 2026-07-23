import json
import re
from dataclasses import dataclass
from typing import Protocol

from policyguard.application.provider_http import (
    ProviderRetryPolicy,
    post_with_retry,
    should_try_backup,
)


class ClaimExtractor(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def extract(self, *, title: str, description: str) -> list[dict[str, object]]: ...


class BaselineClaimExtractor:
    provider_name = "deterministic_baseline"
    model_name = "v0"

    def extract(self, *, title: str, description: str) -> list[dict[str, object]]:
        claims = [value.strip() for value in (title, description) if value.strip()]
        return [
            {"text": claim, "field": "title" if index == 0 else "description", "confidence": 1.0}
            for index, claim in enumerate(claims)
        ]


@dataclass(frozen=True, slots=True)
class OpenAICompatibleClaimExtractor:
    base_url: str
    api_key: str
    model: str
    reasoning_effort: str = "medium"
    timeout_seconds: float = 45
    retry_policy: ProviderRetryPolicy = ProviderRetryPolicy()

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    @property
    def model_name(self) -> str:
        return self.model

    def extract(self, *, title: str, description: str) -> list[dict[str, object]]:
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
                            "Extract verifiable marketing claims from product copy. "
                            'Return JSON only: {"claims":[{"text":string,'
                            '"field":"title"|"description",'
                            '"confidence":number}]}'
                        ),
                    },
                    {"role": "user", "content": f"title: {title}\ndescription: {description}"},
                ],
            },
            timeout=self.timeout_seconds,
            policy=self.retry_policy,
        )
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return self._parse_claims(content)

    @staticmethod
    def _parse_claims(content: str) -> list[dict[str, object]]:
        cleaned = content.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
        if fenced:
            cleaned = fenced.group(1)
        payload = json.loads(cleaned)
        claims = payload.get("claims")
        if not isinstance(claims, list):
            raise ValueError("llm_claims_must_be_a_list")
        normalized: list[dict[str, object]] = []
        for item in claims:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise ValueError("llm_claim_item_invalid")
            field = item.get("field", "description")
            if field not in {"title", "description"}:
                raise ValueError("llm_claim_field_invalid")
            normalized.append(
                {
                    "text": item["text"].strip(),
                    "field": field,
                    "confidence": float(item.get("confidence", 0)),
                }
            )
        return [item for item in normalized if item["text"]]


class FallbackClaimExtractor:
    provider_name = "fallback_chain"

    def __init__(self, *providers: ClaimExtractor) -> None:
        self.providers = providers
        self.model_name = "->".join(provider.model_name for provider in providers)
        self.last_model: str | None = None

    def extract(self, *, title: str, description: str) -> list[dict[str, object]]:
        for provider in self.providers:
            try:
                claims = provider.extract(title=title, description=description)
                self.last_model = provider.model_name
                return claims
            except Exception as exc:
                if not should_try_backup(exc):
                    raise
                continue
        raise RuntimeError("all_claim_extractors_failed")


def configured_claim_extractor(settings) -> ClaimExtractor | None:
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
        OpenAICompatibleClaimExtractor(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=model,
            reasoning_effort=settings.llm_reasoning_effort,
            timeout_seconds=settings.llm_timeout_seconds,
            retry_policy=policy,
        )
        for model in models
    )
    return providers[0] if len(providers) == 1 else FallbackClaimExtractor(*providers)
