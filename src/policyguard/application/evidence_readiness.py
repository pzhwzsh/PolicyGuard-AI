"""Separate collected, structurally valid, legally reviewed, and active evidence."""

import json
from pathlib import Path


def build_evidence_readiness(source_inventory: Path, runtime_snapshot: Path) -> dict:
    sources = json.loads(source_inventory.read_text(encoding="utf-8"))
    runtime = json.loads(runtime_snapshot.read_text(encoding="utf-8"))
    counts = sources["counts"]
    active_documents = int(runtime.get("active_document_count", 0))
    legally_confirmed = int(counts.get("legally_confirmed", 0))
    return {
        "registered_sources": counts["registered_sources"],
        "captured_versions": counts["captured_versions"],
        "parsed_sections": counts["captured_version_sections"],
        "structurally_passed_sources": counts["structurally_passed"],
        "legally_confirmed_sources": legally_confirmed,
        "active_retrieval_documents": active_documents,
        "active_retrieval_chunks": int(runtime.get("active_chunk_count", 0)),
        "legal_advice_ready": legally_confirmed > 0 and active_documents > 0,
        "allowed_claim": (
            "official-source collection and technical retrieval prototype; "
            "not lawyer-verified legal advice"
        ),
        "blocked_claims": [
            "complete global-law coverage",
            "lawyer-verified legal conclusions",
            "production accuracy",
        ],
    }
