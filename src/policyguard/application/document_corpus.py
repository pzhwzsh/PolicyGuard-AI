"""Truthful coverage reporting for real and synthetic PDF evaluation corpora."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

REAL_PROVENANCE = {"official_publication", "consented_customer_document"}


def audit_pdf_manifest(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("samples", []) + payload.get("inventory", [])
    feature_counts: Counter[str] = Counter()
    missing_files = []
    hash_mismatches = []
    real = labeled = accuracy_eligible = 0
    pages = 0
    for row in rows:
        pdf_path = path.parent / row["pdf"]
        if not pdf_path.exists():
            missing_files.append(row["sample_id"])
            continue
        digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        if row.get("sha256") and row["sha256"] != digest:
            hash_mismatches.append(row["sample_id"])
        is_real = row.get("provenance") in REAL_PROVENANCE
        has_labels = bool(row.get("expected_json")) and bool(row.get("pages"))
        real += is_real
        labeled += has_labels
        accuracy_eligible += is_real and has_labels
        pages += int(row.get("page_count", len(row.get("pages", []))))
        for feature in row.get("layout_features", []):
            feature_counts[feature] += 1
    return {
        "document_count": len(rows),
        "real_document_count": real,
        "labeled_document_count": labeled,
        "real_labeled_document_count": accuracy_eligible,
        "declared_page_count": pages,
        "layout_feature_counts": dict(sorted(feature_counts.items())),
        "missing_file_sample_ids": missing_files,
        "hash_mismatch_sample_ids": hash_mismatches,
        "ready_for_general_complex_pdf_claim": (
            real >= 10
            and accuracy_eligible >= 10
            and not missing_files
            and not hash_mismatches
            and {"scanned", "multi_column", "multi_level_table"} <= set(feature_counts)
        ),
        "note": (
            "Unlabeled real PDFs measure parser coverage and latency only; they cannot be used "
            "to report extraction accuracy."
        ),
    }
