"""LLM evidence-support verification with exact-quote enforcement."""

import json
import re
from dataclasses import dataclass

import httpx


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

    def verify_batch(self, cases: list[dict]) -> tuple[list[EvidenceDecision], dict]:
        response = httpx.post(
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
        )
        response.raise_for_status()
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


def configured_evidence_verifier(settings) -> OpenAICompatibleEvidenceVerifier | None:
    if getattr(settings, "app_env", "") == "test":
        return None
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return None
    return OpenAICompatibleEvidenceVerifier(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        timeout_seconds=settings.llm_timeout_seconds,
    )
