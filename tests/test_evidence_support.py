import json

from policyguard.application.evidence_support import OpenAICompatibleEvidenceVerifier


def test_supported_decision_requires_exact_quote() -> None:
    cases = [{"case_id": "a", "query": "q", "evidence": "The exact legal sentence."}]
    content = json.dumps(
        {
            "decisions": [
                {
                    "case_id": "a",
                    "supported": True,
                    "quote": "The exact legal sentence.",
                    "reason": "direct",
                }
            ]
        }
    )
    decision = OpenAICompatibleEvidenceVerifier.parse(content, cases)[0]
    assert decision.supported is True
    assert decision.quote_valid is True


def test_hallucinated_quote_forces_unsupported() -> None:
    cases = [{"case_id": "a", "query": "q", "evidence": "Available evidence."}]
    content = json.dumps(
        {
            "decisions": [
                {
                    "case_id": "a",
                    "supported": True,
                    "quote": "Invented quotation.",
                    "reason": "looks relevant",
                }
            ]
        }
    )
    decision = OpenAICompatibleEvidenceVerifier.parse(content, cases)[0]
    assert decision.supported is False
    assert decision.quote_valid is False
