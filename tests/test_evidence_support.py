import json

from policyguard.application.evidence_support import (
    EvidenceDecision,
    FallbackEvidenceVerifier,
    OpenAICompatibleEvidenceVerifier,
)


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


def test_evidence_verifier_uses_backup_model() -> None:
    class Verifier:
        def __init__(self, model: str, fail: bool) -> None:
            self.model = model
            self.fail = fail

        def verify_batch(self, cases):
            if self.fail:
                raise RuntimeError("offline")
            return [EvidenceDecision("case", False, "", "review", True)], {
                "total_tokens": 3
            }

    chain = FallbackEvidenceVerifier(
        Verifier("primary", True), Verifier("backup", False)
    )

    decisions, usage = chain.verify_batch([{"case_id": "case", "evidence": "text"}])
    assert decisions[0].case_id == "case"
    assert usage["selected_model"] == "backup"
    assert usage["fallback_used"] is True
