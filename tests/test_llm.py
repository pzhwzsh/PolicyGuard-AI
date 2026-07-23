import json

import pytest

from policyguard.application.llm import (
    BaselineClaimExtractor,
    FallbackClaimExtractor,
    OpenAICompatibleClaimExtractor,
)


def test_baseline_claim_extractor_is_explicit_and_repeatable() -> None:
    claims = BaselineClaimExtractor().extract(title="国家级护肤品", description="适合日常使用")

    assert claims == [
        {"text": "国家级护肤品", "field": "title", "confidence": 1.0},
        {"text": "适合日常使用", "field": "description", "confidence": 1.0},
    ]


def test_llm_claim_parser_accepts_json_fence() -> None:
    content = "```json\n" + json.dumps(
        {"claims": [{"text": "clinically proven", "field": "title", "confidence": 0.9}]}
    ) + "\n```"

    claims = OpenAICompatibleClaimExtractor._parse_claims(content)

    assert claims[0]["text"] == "clinically proven"
    assert claims[0]["confidence"] == pytest.approx(0.9)


def test_llm_claim_parser_rejects_invalid_shape() -> None:
    with pytest.raises(ValueError, match="llm_claims_must_be_a_list"):
        OpenAICompatibleClaimExtractor._parse_claims("{\"claims\": {}}")


def test_claim_extractor_uses_backup_model_before_rule_fallback() -> None:
    class Extractor:
        provider_name = "test"

        def __init__(self, model_name: str, fail: bool) -> None:
            self.model_name = model_name
            self.fail = fail

        def extract(self, *, title: str, description: str):
            if self.fail:
                raise RuntimeError("offline")
            return [{"text": title, "field": "title", "confidence": 0.9}]

    chain = FallbackClaimExtractor(
        Extractor("primary", True), Extractor("backup", False)
    )

    assert chain.extract(title="claim", description="")[0]["text"] == "claim"
    assert chain.last_model == "backup"
