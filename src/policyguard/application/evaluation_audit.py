"""Evaluation-set governance and deterministic failure attribution.

This module does not create legal labels.  It makes dataset leakage and missing
human review visible before a result can be described as held-out evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

REVIEWED_LABELS = {"independent_human_reviewed", "lawyer_reviewed"}


def normalized_query(value: str) -> str:
    return " ".join(value.casefold().split())


def query_fingerprint(value: str) -> str:
    return hashlib.sha256(normalized_query(value).encode("utf-8")).hexdigest()


def load_queries(paths: list[Path]) -> dict[str, list[str]]:
    fingerprints: dict[str, list[str]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for sample in payload.get("samples", []):
            fingerprint = query_fingerprint(sample["query"])
            fingerprints.setdefault(fingerprint, []).append(str(path))
    return fingerprints


def audit_holdout_dataset(path: Path, development_paths: list[Path]) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    samples = payload.get("samples", [])
    development = load_queries(development_paths)
    seen: set[str] = set()
    duplicates: list[str] = []
    leakage: list[str] = []
    incomplete: list[str] = []
    for sample in samples:
        sample_id = str(sample.get("id", "missing-id"))
        required = ("query", "jurisdiction", "answerable", "review_status")
        if any(key not in sample for key in required):
            incomplete.append(sample_id)
            continue
        if sample["answerable"] and not sample.get("expected_section_id"):
            incomplete.append(sample_id)
        if sample.get("review_status") in REVIEWED_LABELS and not (
            sample.get("reviewer_alias") and sample.get("reviewed_at")
        ):
            incomplete.append(sample_id)
        fingerprint = query_fingerprint(sample["query"])
        if fingerprint in seen:
            duplicates.append(sample_id)
        seen.add(fingerprint)
        if fingerprint in development:
            leakage.append(sample_id)
    reviewed = sum(sample.get("review_status") in REVIEWED_LABELS for sample in samples)
    frozen = bool(payload.get("frozen_at")) and payload.get("split") == "holdout"
    independently_attested = payload.get("independent_reviewer_attestation") is True
    ready = (
        len(samples) >= int(payload.get("minimum_sample_count", 50))
        and reviewed == len(samples)
        and frozen
        and independently_attested
        and not duplicates
        and not leakage
        and not incomplete
    )
    return {
        "dataset": payload.get("name", path.stem),
        "sample_count": len(samples),
        "minimum_sample_count": int(payload.get("minimum_sample_count", 50)),
        "reviewed_count": reviewed,
        "independent_review_complete": reviewed == len(samples) and bool(samples),
        "frozen_holdout": frozen,
        "independent_reviewer_attestation": independently_attested,
        "duplicate_sample_ids": duplicates,
        "development_leakage_sample_ids": leakage,
        "incomplete_sample_ids": incomplete,
        "ready_for_reportable_holdout_metrics": ready,
        "note": "Readiness validates provenance and isolation, not legal correctness.",
    }


def classify_retrieval_failures(rows: list[dict]) -> dict:
    """Classify observable retrieval failures without guessing hidden causes."""
    classified = []
    counts: Counter[str] = Counter()
    for row in rows:
        expected = row.get("expected_section_id")
        retrieved = row.get("retrieved_section_ids", [])
        answerable = bool(row.get("answerable", expected is not None))
        predicted_answerable = row.get("predicted_answerable")
        if answerable and expected not in retrieved:
            reason = "recall_miss"
        elif answerable and retrieved and retrieved[0] != expected:
            reason = "ranking_miss"
        elif not answerable and predicted_answerable is True:
            reason = "false_answer"
        elif answerable and predicted_answerable is False:
            reason = "false_abstention"
        elif row.get("citation_supported") is False:
            reason = "citation_mismatch"
        elif row.get("rewrite_used") and row.get("original_hit") and not row.get("rewritten_hit"):
            reason = "query_rewrite_drift"
        else:
            reason = "pass_or_unclassified"
        counts[reason] += 1
        classified.append({"sample_id": row.get("id") or row.get("case_id"), "class": reason})
    return {"counts": dict(sorted(counts.items())), "samples": classified}
