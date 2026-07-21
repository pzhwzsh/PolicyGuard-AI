import json
from pathlib import Path

from policyguard.scripts.benchmark_abstention import _best_threshold


def test_threshold_balances_answerable_and_no_answer_scores() -> None:
    result = _best_threshold([0.6, 0.7, 0.8], [0.1, 0.2, 0.3])
    assert 0.3 < result["threshold"] < 0.6
    assert result["balanced_accuracy"] == 1.0


def test_no_answer_dataset_is_explicit_and_unique() -> None:
    root = Path(__file__).parents[1]
    samples = json.loads(
        (root / "data/evaluation/rag-no-answer-v1.json").read_text(encoding="utf-8")
    )["samples"]
    assert len(samples) == 15
    assert len({(sample["query"], sample["jurisdiction"]) for sample in samples}) == 15
    assert all(sample["answerable"] is False for sample in samples)


def test_near_domain_no_answer_set_is_held_out_and_unique() -> None:
    root = Path(__file__).parents[1]
    calibration = json.loads(
        (root / "data/evaluation/rag-no-answer-v1.json").read_text(encoding="utf-8")
    )["samples"]
    heldout = json.loads(
        (root / "data/evaluation/rag-no-answer-near-v1.json").read_text(encoding="utf-8")
    )["samples"]
    calibration_keys = {(sample["query"], sample["jurisdiction"]) for sample in calibration}
    heldout_keys = {(sample["query"], sample["jurisdiction"]) for sample in heldout}
    assert len(heldout) == 20
    assert len(heldout_keys) == 20
    assert calibration_keys.isdisjoint(heldout_keys)
    assert all(sample["answerable"] is False for sample in heldout)
