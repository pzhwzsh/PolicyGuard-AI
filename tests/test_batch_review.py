import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from policyguard.api.main import create_app
from policyguard.application.batch_review import (
    _safe_cell,
    process_batch_review,
    read_batch_rows,
)
from policyguard.infrastructure.database import Database

FIELDS = ["external_id", "category", "title", "description", "markets"]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_csv_normalizes_market_separators_and_preserves_zero(tmp_path: Path) -> None:
    path = tmp_path / "products.csv"
    write_csv(path, [{
        "external_id": 0,
        "category": "beauty",
        "title": "Product",
        "description": "Description",
        "markets": "cn，us；EU",
    }])

    rows = read_batch_rows(path)

    assert rows[0]["external_id"] == "0"
    assert rows[0]["markets"] == ["CN", "US", "EU"]


def test_xlsx_ignores_blank_rows(tmp_path: Path) -> None:
    path = tmp_path / "products.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(FIELDS)
    sheet.append(["SKU-1", "all", "Title", "Description", "CN"])
    sheet.append([None, None, None, None, None])
    workbook.save(path)

    assert len(read_batch_rows(path)) == 1


def test_batch_validation_rejects_bad_schema_market_and_row_limit(tmp_path: Path) -> None:
    missing = tmp_path / "missing.csv"
    missing.write_text("title,markets\nTitle,CN\n", encoding="utf-8")
    with pytest.raises(ValueError, match="^batch_missing_columns:"):
        read_batch_rows(missing)

    invalid = tmp_path / "invalid.csv"
    write_csv(invalid, [{
        "external_id": "1", "category": "all", "title": "Title",
        "description": "", "markets": "GB",
    }])
    with pytest.raises(ValueError, match="^batch_markets_invalid:2$"):
        read_batch_rows(invalid)

    limited = tmp_path / "limited.csv"
    write_csv(limited, [
        {"external_id": str(index), "category": "all", "title": "Title",
         "description": "", "markets": "CN"}
        for index in range(2)
    ])
    with pytest.raises(ValueError, match="^batch_row_limit_exceeded$"):
        read_batch_rows(limited, max_rows=1)


def test_process_batch_resumes_and_exports_formula_safe_csv(tmp_path: Path) -> None:
    source = tmp_path / "products.csv"
    write_csv(source, [
        {"external_id": "=1+1", "category": "all", "title": "First",
         "description": "", "markets": "CN"},
        {"external_id": "SKU-2", "category": "all", "title": "Second",
         "description": "", "markets": "US"},
    ])
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    checkpoint = {
        "row_number": 2, "external_id": "=1+1", "workflow_id": "existing-run",
        "status": "needs_more_evidence", "evidence_count": 0, "markets": "CN",
    }
    (output_dir / "results.jsonl").write_text(
        json.dumps(checkpoint) + "\n", encoding="utf-8"
    )
    database = Database(f"sqlite:///{(tmp_path / 'batch.db').as_posix()}")
    database.initialize()

    with database.session_factory() as session:
        result = process_batch_review(session, source, output_dir)

    assert result["row_count"] == 2
    assert result["completed_count"] == 2
    assert len((output_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    with Path(result["output_path"]).open("r", encoding="utf-8-sig", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert exported[0]["external_id"] == "'=1+1"
    assert _safe_cell("@SUM(A1)") == "'@SUM(A1)"


def test_batch_api_enqueues_idempotently_and_rejects_bad_extension(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    body = (
        b"external_id,category,title,description,markets\n"
        b"SKU-1,all,Title,Description,CN\n"
    )
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/batches/review", files={"file": ("items.csv", body, "text/csv")}
        )
        repeated = client.post(
            "/api/v1/batches/review", files={"file": ("renamed.csv", body, "text/csv")}
        )
        unsupported = client.post(
            "/api/v1/batches/review", files={"file": ("items.txt", body, "text/plain")}
        )

    assert first.status_code == 202
    assert repeated.json()["id"] == first.json()["id"]
    assert unsupported.status_code == 415


def test_batch_template_is_downloadable(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{(tmp_path / 'template.db').as_posix()}")
    with TestClient(app) as client:
        response = client.get("/api/v1/batches/template")
    assert response.status_code == 200
    assert "external_id,category,title,description,markets" in response.text
    assert "attachment" in response.headers["content-disposition"]
