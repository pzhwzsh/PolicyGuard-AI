from pathlib import Path

from policyguard.application.evaluation_suite import load_evaluation_suite

ROOT = Path(__file__).parents[1]


def test_unified_rag_suite_has_eighty_unique_development_questions() -> None:
    suite = load_evaluation_suite(ROOT / "data/evaluation/suite-v1.json")
    assert len(suite["samples"]) == 80
    assert sum(item["answerable"] for item in suite["samples"]) == 45
    assert sum(not item["answerable"] for item in suite["samples"]) == 35
