import json

import pytest

from policyguard.application.llm import BaselineClaimExtractor, OpenAICompatibleClaimExtractor


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

