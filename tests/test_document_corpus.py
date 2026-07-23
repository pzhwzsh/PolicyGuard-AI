import hashlib
import json
from pathlib import Path

from policyguard.application.document_corpus import audit_pdf_manifest


def test_pdf_audit_does_not_treat_unlabeled_real_pdf_as_accuracy_evidence(tmp_path: Path) -> None:
    pdf = tmp_path / "official.pdf"
    pdf.write_bytes(b"%PDF-test")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"inventory": [{
        "sample_id": "official", "pdf": pdf.name, "provenance": "official_publication",
        "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(), "page_count": 5,
        "layout_features": ["scanned", "multi_level_table"],
    }]}), encoding="utf-8")
    report = audit_pdf_manifest(manifest)
    assert report["real_document_count"] == 1
    assert report["real_labeled_document_count"] == 0
    assert report["ready_for_general_complex_pdf_claim"] is False
