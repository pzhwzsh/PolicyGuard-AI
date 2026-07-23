import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from policyguard.api.main import create_app
from policyguard.application.table_cleaning import TableCleaningWorkspace, analyze_table


def multi_sheet_workbook(path: Path) -> None:
    workbook = Workbook()
    products = workbook.active
    products.title = "Products"
    products.append(["Export catalog", None, None, None, None])
    products.append(["SKU", "商品名称", "产品描述", "类目", "目标市场"])
    products.append(["001", "  Clean   title  ", "Line 1\nLine 2", "Beauty", "中国，US"])
    products.append(["002", "=HYPERLINK(\"https://bad\")", "Description", "", "EU"])
    ignored = workbook.create_sheet("Notes")
    ignored.append(["This sheet has no recognized product headers"])
    workbook.save(path)


def test_xlsx_cleaning_detects_header_maps_aliases_and_preserves_provenance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "products.xlsx"
    multi_sheet_workbook(path)

    analysis = analyze_table(path)

    products = next(sheet for sheet in analysis["sheets"] if sheet["name"] == "Products")
    assert products["header_row"] == 2
    assert analysis["skipped_sheets"] == [
        {"name": "Notes", "reason": "table_header_not_detected"}
    ]
    assert products["mapping"]["商品名称"] == "title"
    assert analysis["rows"][0]["cleaned"]["title"] == "Clean title"
    assert analysis["rows"][0]["cleaned"]["markets"] == "CN,US"
    assert analysis["rows"][0]["source_sheet"] == "Products"
    assert analysis["rows"][0]["source_row"] == 3
    assert analysis["summary"]["model_call_count"] == 0
    assert analysis["summary"]["estimated_model_tokens"] == 0
    assert any(
        issue["code"] == "formula_cell_not_evaluated"
        for issue in analysis["rows"][1]["issues"]
    )


def test_manual_mapping_and_partial_confirmation_export_formula_safe_report(
    tmp_path: Path,
) -> None:
    source = tmp_path / "custom.csv"
    source.write_text(
        "编号,名称,详情,区域\n001,Good,Description,CN\n001,=2+2,Other,GB\n",
        encoding="utf-8-sig",
    )
    workspace = TableCleaningWorkspace(tmp_path / "tables")
    staged = workspace.stage(source.read_bytes(), source.name)
    mapping = {
        "CSV": {
            "编号": "external_id", "名称": "title",
            "详情": "description", "区域": "markets",
        }
    }

    with pytest.raises(RuntimeError, match="blocking_errors"):
        workspace.confirm(
            staged["table_id"], expected_revision=0, reviewer="reviewer",
            field_mappings=mapping, allow_partial=False,
        )
    confirmed = workspace.confirm(
        staged["table_id"], expected_revision=0, reviewer="reviewer",
        field_mappings=mapping, allow_partial=True,
    )

    cleaned_path = Path(confirmed["manifest"]["cleaned_path"])
    with cleaned_path.open("r", encoding="utf-8-sig", newline="") as handle:
        cleaned = list(csv.DictReader(handle))
    assert len(cleaned) == 1
    assert cleaned[0]["external_id"] == "001"
    report = Path(confirmed["manifest"]["report_path"]).read_text(encoding="utf-8-sig")
    assert "duplicate_external_id" in report
    assert "'=2+2" in report


def test_cleaning_preview_and_confirmation_api_enqueue_only_after_confirmation(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    body = (
        "货号,商品名称,商品描述,商品类目,站点\n"
        "SKU-1,Title,Description,Beauty,美国\n"
    ).encode("utf-8-sig")
    app = create_app(f"sqlite:///{(tmp_path / 'cleaning.db').as_posix()}")
    with TestClient(app) as client:
        preview = client.post(
            "/api/v1/batches/clean-preview",
            files={"file": ("products.csv", body, "text/csv")},
        )
        jobs_before = client.get("/api/v1/jobs").json()
        payload = preview.json()
        confirmed = client.post(
            f"/api/v1/batches/cleaning/{payload['table_id']}/confirm",
            json={
                "expected_revision": payload["revision"],
                "reviewer": "content-owner",
                "field_mappings": {},
                "allow_partial": False,
            },
        )
        report = client.get(confirmed.json()["report_url"])

    assert preview.status_code == 201
    assert payload["summary"]["valid_row_count"] == 1
    assert jobs_before == []
    assert confirmed.status_code == 202
    assert confirmed.json()["job"]["status"] == "queued"
    assert report.status_code == 200
    assert "SKU-1" in report.text


def test_table_limits_are_enforced_before_unbounded_materialization(tmp_path: Path) -> None:
    too_wide = tmp_path / "wide.csv"
    too_wide.write_text(",".join(f"column-{index}" for index in range(101)), encoding="utf-8")
    with pytest.raises(ValueError, match="column_limit"):
        analyze_table(too_wide)

    too_long = tmp_path / "long.csv"
    rows = ["title,markets", *(f"Product {index},CN" for index in range(1001))]
    too_long.write_text("\n".join(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="row_limit"):
        analyze_table(too_long)


def test_confirmation_rejects_tampered_source_empty_output_and_repeat(tmp_path: Path) -> None:
    workspace = TableCleaningWorkspace(tmp_path / "tables")
    invalid = workspace.stage(b"title,markets\n,GB\n", "invalid.csv")
    with pytest.raises(RuntimeError, match="no_valid_rows"):
        workspace.confirm(
            invalid["table_id"], expected_revision=0, reviewer="reviewer",
            field_mappings={}, allow_partial=True,
        )

    valid = workspace.stage(b"title,markets\nProduct,CN\n", "valid.csv")
    confirmed = workspace.confirm(
        valid["table_id"], expected_revision=0, reviewer="reviewer",
        field_mappings={}, allow_partial=False,
    )
    with pytest.raises(RuntimeError, match="already_confirmed"):
        workspace.confirm(
            valid["table_id"], expected_revision=confirmed["manifest"]["revision"],
            reviewer="reviewer", field_mappings={}, allow_partial=False,
        )

    tampered = workspace.stage(b"title,markets\nOther,CN\n", "tampered.csv")
    manifest_path = tmp_path / "tables" / tampered["table_id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["original_path"] = str((tmp_path / "outside.csv").resolve())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="source_invalid"):
        workspace.confirm(
            tampered["table_id"], expected_revision=0, reviewer="reviewer",
            field_mappings={}, allow_partial=False,
        )


def test_duplicate_mapping_is_blocking_even_when_partial_is_allowed(tmp_path: Path) -> None:
    workspace = TableCleaningWorkspace(tmp_path / "tables")
    staged = workspace.stage(
        b"title,markets,extra\nProduct,CN,Second title\n", "duplicate.csv"
    )
    with pytest.raises(RuntimeError, match="mapping_invalid"):
        workspace.confirm(
            staged["table_id"], expected_revision=0, reviewer="reviewer",
            field_mappings={"CSV": {"extra": "title"}}, allow_partial=True,
        )


def test_malformed_workbook_is_reported_as_invalid_input(tmp_path: Path) -> None:
    workspace = TableCleaningWorkspace(tmp_path / "tables")
    with pytest.raises(ValueError, match="batch_file_invalid"):
        workspace.stage(b"not an xlsx archive", "broken.xlsx")
