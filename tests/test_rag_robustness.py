import json
from pathlib import Path


def test_robustness_set_is_separate_and_marks_synthetic_variants() -> None:
    path = Path(__file__).parents[1] / "data/evaluation/rag-robustness-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["sample_count"] == 180
    assert len(payload["samples"]) == 180
    assert sum(item["synthetic"] for item in payload["samples"]) == 135
    assert "never as independent human labels" in payload["labeling"]
