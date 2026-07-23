from pathlib import Path

from policyguard.application.evidence_readiness import build_evidence_readiness

ROOT = Path(__file__).parents[1]


def test_current_release_cannot_claim_legal_advice_readiness() -> None:
    report = build_evidence_readiness(
        ROOT / "data/evidence/v1/source-inventory.json",
        ROOT / "data/evidence/v1/runtime-snapshot.json",
    )
    assert report["registered_sources"] == 11
    assert report["legally_confirmed_sources"] == 0
    assert report["active_retrieval_documents"] == 3
    assert report["legal_advice_ready"] is False
