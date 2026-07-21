import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_hash(path: Path) -> str:
    content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return sha256(content).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _latest_staged_manifests(staged_root: Path) -> tuple[list[dict], dict[str, dict]]:
    manifests = [
        _read_json(path) | {"_directory": path.parent}
        for path in staged_root.glob("*/*/manifest.json")
    ]
    latest: dict[str, dict] = {}
    for item in sorted(manifests, key=lambda value: value["created_at"], reverse=True):
        latest.setdefault(item["source_id"], item)
    return manifests, latest


def build_source_release(root: Path) -> tuple[dict, dict[str, dict]]:
    registry = _read_json(root / "config/source_registry.json")
    all_manifests, latest = _latest_staged_manifests(root / "data/update-state/staged")
    entries = []
    releases = {}
    latest_sections = 0

    for source in registry["sources"]:
        source_id = source["id"]
        manifest = latest.get(source_id)
        if manifest is None:
            entries.append({
                "source_id": source_id,
                "jurisdiction": source["jurisdiction"],
                "source_url": source["source_url"],
                "release_status": "missing",
            })
            continue

        policy_path = manifest["_directory"] / "policy.json"
        policy = _read_json(policy_path)
        sections = policy.get("sections", [])
        section_ids = [item.get("section_id") for item in sections]
        errors = []
        if manifest["content_hash"] != manifest["_directory"].name:
            errors.append("content_hash_directory_mismatch")
        if policy.get("source_url") != source["source_url"]:
            errors.append("source_url_mismatch")
        if len(sections) != manifest["section_count"]:
            errors.append("section_count_mismatch")
        if len(section_ids) != len(set(section_ids)):
            errors.append("duplicate_section_id")
        if any(not item.get("text", "").strip() for item in sections):
            errors.append("empty_section_text")

        release = {
            "schema_version": "policyguard-source-evidence-v1",
            "evidence_status": "official_source_structurally_parsed_pending_legal_review",
            "source": {
                key: source[key]
                for key in (
                    "id", "title", "jurisdiction", "publisher", "source_url", "format",
                    "source_language", "ingestion_mode", "activation_policy",
                )
                if key in source
            } | {"reuse_review_status": "official_publication_terms_not_reviewed"},
            "capture": {
                "content_hash": manifest["content_hash"],
                "created_at": manifest["created_at"],
                "retrieved_at": policy.get("retrieved_at"),
                "section_count": len(sections),
                "structural_review_status": manifest.get("structural_review_status", "unknown"),
                "legal_review_status": manifest.get("legal_review_status", "pending"),
                "eligible_for_activation": manifest.get("eligible_for_activation", False),
                "short_section_rate": manifest.get("short_section_rate"),
                "replacement_character_count": sum(
                    item.get("text", "").count("\ufffd") for item in sections
                ),
                "validation_errors": errors,
            },
            "document": policy,
        }
        releases[source_id] = release
        latest_sections += len(sections)
        entries.append({
            "source_id": source_id,
            "jurisdiction": source["jurisdiction"],
            "publisher": source["publisher"],
            "source_url": source["source_url"],
            "ingestion_mode": source["ingestion_mode"],
            "content_hash": manifest["content_hash"],
            "retrieved_at": policy.get("retrieved_at"),
            "section_count": len(sections),
            "structural_review_status": manifest.get("structural_review_status", "unknown"),
            "legal_review_status": manifest.get("legal_review_status", "pending"),
            "eligible_for_activation": manifest.get("eligible_for_activation", False),
            "reuse_review_status": "official_publication_terms_not_reviewed",
            "validation_errors": errors,
            "release_path": f"sources/{source_id}.json",
        })

    inventory = {
        "schema_version": "policyguard-source-inventory-v1",
        "as_of": "2026-07-21",
        "scope": "CN/US/EU advertising and selected product/channel regulation sources",
        "automatic_approval": False,
        "counts": {
            "registered_sources": len(registry["sources"]),
            "captured_versions": len(all_manifests),
            "captured_version_sections": sum(item["section_count"] for item in all_manifests),
            "latest_release_sources": len(releases),
            "latest_release_sections": latest_sections,
            "structurally_passed": sum(
                item.get("structural_review_status") == "passed" for item in entries
            ),
            "structurally_blocked": sum(
                item.get("structural_review_status") == "blocked" for item in entries
            ),
            "legally_confirmed": sum(
                item.get("legal_review_status") == "confirmed" for item in entries
            ),
        },
        "limitations": [
            "A published source copy is evidence of collection and parsing, not legal approval.",
            "Catalog pages are retained for provenance but are not eligible legal documents.",
            "Coverage is bounded and is not a complete corpus of global advertising law.",
        ],
        "sources": entries,
    }
    return inventory, releases


def build_dataset_inventory(root: Path) -> dict:
    datasets = []
    for path in sorted((root / "data/evaluation").glob("*.json")):
        payload = _read_json(path)
        if isinstance(payload, list):
            sample_count = len(payload)
            label_status = "development_unverified"
            name = path.stem
        else:
            rows = payload.get("samples", payload.get("cases", payload.get("queries", [])))
            sample_count = len(rows) if isinstance(rows, list) else 0
            label_status = payload.get("label_status", "development_unverified")
            name = payload.get("name", path.stem)
        human_verified = payload.get("ai_source_audit", {}).get("human_verified_samples", 0) \
            if isinstance(payload, dict) else 0
        datasets.append({
            "name": name,
            "path": f"data/evaluation/{path.name}",
            "sha256": _file_hash(path),
            "sample_count": sample_count,
            "label_status": label_status,
            "human_verified_samples": human_verified,
        })
    return {
        "schema_version": "policyguard-dataset-inventory-v1",
        "independent_sample_count": None,
        "independent_sample_count_reason": (
            "Some suites and robustness rows reuse or derive from the same authored questions."
        ),
        "human_verified_samples": sum(item["human_verified_samples"] for item in datasets),
        "datasets": datasets,
    }


def _artifact(root: Path, name: str) -> dict | list | None:
    path = root / "data/benchmarks" / name
    return _read_json(path) if path.is_file() else None


def build_benchmark_release(root: Path) -> dict:
    artifact_names = (
        "local-embedding-results.json", "local-embedding-hard-v1.json",
        "local-rerank-results.json", "agent-vs-pipeline.json",
        "query-rewrite-hard-v1.json", "cross-language-abstention-v1.json",
        "abstention-jina-hard-v1.json", "remediation-quality-v1.json",
        "pdf-quality.json", "external-smoke.json",
        "portfolio-scale-v1.json", "synthetic-pdf-scale-v1.json",
    )
    embedding_easy = _artifact(root, "local-embedding-results.json") or []
    embedding_hard = _artifact(root, "local-embedding-hard-v1.json") or []
    rerank = _artifact(root, "local-rerank-results.json") or []
    agent = _artifact(root, "agent-vs-pipeline.json") or {}
    rewrite = _artifact(root, "query-rewrite-hard-v1.json") or {}
    abstention = _artifact(root, "cross-language-abstention-v1.json") or {}
    heldout = _artifact(root, "abstention-jina-hard-v1.json") or {}
    remediation = _artifact(root, "remediation-quality-v1.json") or {}
    pdf = _artifact(root, "pdf-quality.json") or {}
    external = _artifact(root, "external-smoke.json") or {}
    portfolio = _artifact(root, "portfolio-scale-v1.json") or {}
    synthetic_pdf = _artifact(root, "synthetic-pdf-scale-v1.json") or {}

    def model_rows(rows: list) -> list[dict]:
        keys = (
            "model", "status", "sample_count", "hit_rate_at_k", "mean_reciprocal_rank",
            "mean_latency_ms", "p95_latency_ms", "failures",
        )
        return [{key: row.get(key) for key in keys if key in row} for row in rows]

    external_checks = []
    for item in external.get("checks", []):
        status = item.get("status")
        access_blocked = item.get("status_code") in {401, 403, 429}
        if item.get("name", "").startswith("source:") and access_blocked:
            status = "blocked"
        external_checks.append({
            key: value for key, value in {
                "name": item.get("name"), "status": status,
                "status_code": item.get("status_code"), "latency_ms": item.get("latency_ms"),
            }.items() if value is not None
        })

    return {
        "schema_version": "policyguard-benchmark-evidence-v1",
        "as_of": "2026-07-21",
        "truthfulness": {
            "human_verified_samples": 0,
            "cost_usd": None,
            "cost_reason": "Provider pricing was not configured.",
            "development_only": True,
        },
        "input_artifacts": {
            name: _file_hash(root / "data/benchmarks" / name)
            for name in artifact_names
            if (root / "data/benchmarks" / name).is_file()
        },
        "embedding": {
            "easy_15": model_rows(embedding_easy),
            "hard_30": model_rows(embedding_hard),
        },
        "rerank_hard_30": model_rows(rerank),
        "query_rewrite_hard_30": {
            key: rewrite.get(key) for key in (
                "model", "embedding", "rewrite_request_count", "rewrite_total_tokens",
                "original", "canonical_only", "multi_query_rrf", "selective_multi_query_rrf",
            ) if key in rewrite
        },
        "abstention": {
            "same_set_calibration": abstention.get("best", {}),
            "same_set_positive_count": abstention.get("positive_count"),
            "same_set_negative_count": abstention.get("negative_count"),
            "heldout_threshold": heldout.get("heldout_threshold"),
            "heldout_near_domain_count": heldout.get("near_domain_heldout_count"),
            "heldout_near_domain_specificity": heldout.get("near_domain_specificity"),
        },
        "agent_vs_pipeline": {
            "pipeline": agent.get("pipeline", {}).get("metrics", {}),
            "agent": agent.get("agent", {}).get("metrics", {}),
            "agent_cases": agent.get("agent", {}).get("cases", []),
        },
        "remediation": remediation,
        "pdf": pdf,
        "portfolio_scale": portfolio,
        "synthetic_pdf_scale": synthetic_pdf,
        "external_smoke": {
            "checks": external_checks,
            "contains_credentials": False,
        },
        "limitations": [
            "Metrics are development evidence, not production or legal-accuracy claims.",
            "The abstention threshold generalizes poorly to the held-out near-domain set.",
            "PDF quality covers one official document and two labeled pages.",
        ],
    }


def build_runtime_snapshot(root: Path) -> dict:
    database_path = root / "data/policyguard.db"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        tables = (
            "policy_documents", "policy_chunks", "policy_scopes", "compliance_checks",
            "findings", "workflow_runs", "workflow_events", "agent_memories",
            "evaluation_reviews", "background_jobs", "audit_logs",
        )
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
        active_documents = [dict(row) for row in connection.execute(
            "SELECT d.id, d.title, d.publisher, d.source_url, d.version, "
            "COUNT(c.id) AS chunk_count FROM policy_documents d "
            "LEFT JOIN policy_chunks c ON c.document_id = d.id WHERE d.active = 1 "
            "GROUP BY d.id, d.title, d.publisher, d.source_url, d.version ORDER BY d.id"
        )]
    finally:
        connection.close()
    return {
        "schema_version": "policyguard-runtime-snapshot-v1",
        "as_of": "2026-07-22",
        "environment": "local_development",
        "database_file_published": False,
        "counts": counts,
        "active_document_count": len(active_documents),
        "active_chunk_count": sum(item["chunk_count"] for item in active_documents),
        "active_documents": active_documents,
        "limitations": [
            "Zero operational rows mean the repository has no production traffic evidence yet.",
            "The mutable local database is excluded because it may contain uploaded content.",
        ],
    }


def publish_evidence_bundle(root: Path, target: Path) -> dict:
    source_inventory, releases = build_source_release(root)
    _write_json(target / "source-inventory.json", source_inventory)
    for source_id, payload in releases.items():
        _write_json(target / "sources" / f"{source_id}.json", payload)
    for item in source_inventory["sources"]:
        if "release_path" in item:
            item["release_sha256"] = _file_hash(target / item["release_path"])
    _write_json(target / "source-inventory.json", source_inventory)
    _write_json(target / "dataset-inventory.json", build_dataset_inventory(root))
    _write_json(target / "benchmarks.json", build_benchmark_release(root))
    _write_json(target / "runtime-snapshot.json", build_runtime_snapshot(root))
    return source_inventory


def validate_published_bundle(target: Path) -> list[str]:
    errors = []
    inventory = _read_json(target / "source-inventory.json")
    if inventory.get("automatic_approval") is not False:
        errors.append("automatic_approval_must_be_false")
    for item in inventory.get("sources", []):
        release_path = item.get("release_path")
        if not release_path:
            errors.append(f"missing_release:{item.get('source_id')}")
            continue
        path = target / release_path
        if not path.is_file() or _file_hash(path) != item.get("release_sha256"):
            errors.append(f"release_hash_mismatch:{item.get('source_id')}")
            continue
        payload = _read_json(path)
        serialized = json.dumps(payload, ensure_ascii=False)
        if ":\\" in serialized or "api_key" in serialized.casefold():
            errors.append(f"non_portable_or_secret_field:{item.get('source_id')}")
        capture = payload.get("capture", {})
        if capture.get("legal_review_status") not in {"pending", "confirmed"}:
            errors.append(f"invalid_legal_review_status:{item.get('source_id')}")
        if capture.get("validation_errors"):
            errors.append(f"source_validation_failed:{item.get('source_id')}")
    dataset_inventory = _read_json(target / "dataset-inventory.json")
    if dataset_inventory.get("human_verified_samples") != 0:
        errors.append("unexpected_human_verified_claim")
    runtime = _read_json(target / "runtime-snapshot.json")
    if runtime.get("database_file_published") is not False:
        errors.append("mutable_database_must_not_be_published")
    benchmarks = _read_json(target / "benchmarks.json")
    portfolio = benchmarks.get("portfolio_scale", {})
    if portfolio.get("rag", {}).get("sample_count") != 120:
        errors.append("portfolio_rag_scale_mismatch")
    if portfolio.get("workflow", {}).get("case_count") != 100:
        errors.append("portfolio_workflow_scale_mismatch")
    synthetic_pdf = benchmarks.get("synthetic_pdf_scale", {})
    if synthetic_pdf.get("document_count") != 20 or synthetic_pdf.get("page_count") != 200:
        errors.append("synthetic_pdf_scale_mismatch")
    if synthetic_pdf.get("data_type") != "programmatically_generated_not_real_regulation":
        errors.append("synthetic_pdf_origin_missing")
    return errors
