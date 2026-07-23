import json
from pathlib import Path

import pytest

from policyguard.application.evaluation_workbench import (
    dataset_fingerprint,
    validate_evaluation_path,
)


def test_evaluation_dataset_is_scoped_and_hashed(tmp_path: Path) -> None:
    dataset = tmp_path / "data/evaluation/test.json"
    dataset.parent.mkdir(parents=True)
    dataset.write_text(json.dumps({"samples": [{"query": "q"}]}), encoding="utf-8")
    selected = validate_evaluation_path(tmp_path, "data/evaluation/test.json")
    assert selected == dataset
    assert len(dataset_fingerprint(selected)) == 64


def test_evaluation_dataset_rejects_paths_outside_evaluation_root(tmp_path: Path) -> None:
    outside = tmp_path / "private.json"
    outside.write_text(json.dumps({"samples": [{"query": "q"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="evaluation_dataset_invalid"):
        validate_evaluation_path(tmp_path, "private.json")
