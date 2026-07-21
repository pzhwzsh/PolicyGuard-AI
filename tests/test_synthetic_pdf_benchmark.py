from pathlib import Path

from policyguard.application.synthetic_pdf_benchmark import run_synthetic_pdf_benchmark


def test_synthetic_pdf_benchmark_preserves_markers_and_declares_origin(tmp_path: Path) -> None:
    report = run_synthetic_pdf_benchmark(tmp_path, documents=2, pages=2)
    assert report["document_count"] == 2
    assert report["page_count"] == 4
    assert report["mean_marker_recall"] == 1.0
    assert report["data_type"] == "programmatically_generated_not_real_regulation"
    assert all(len(item["sha256"]) == 64 for item in report["documents"])
    assert len(list((tmp_path / "data/evaluation/pdf/synthetic-v1").glob("*.pdf"))) == 2
