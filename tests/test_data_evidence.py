import json
import sqlite3
from hashlib import sha256
from pathlib import Path

from policyguard.application.data_evidence import (
    publish_evidence_bundle,
    validate_published_bundle,
)

ROOT = Path(__file__).parents[1]


def test_published_evidence_is_complete_portable_and_honest() -> None:
    target = ROOT / "data/evidence/v1"
    assert validate_published_bundle(target) == []
    sources = json.loads((target / "source-inventory.json").read_text(encoding="utf-8"))
    datasets = json.loads((target / "dataset-inventory.json").read_text(encoding="utf-8"))
    benchmarks = json.loads((target / "benchmarks.json").read_text(encoding="utf-8"))
    runtime = json.loads((target / "runtime-snapshot.json").read_text(encoding="utf-8"))

    assert sources["counts"]["registered_sources"] == 11
    assert sources["counts"]["latest_release_sources"] == 11
    assert sources["counts"]["latest_release_sections"] == 5468
    assert sources["counts"]["structurally_passed"] == 0
    assert sources["counts"]["structurally_blocked"] == 11
    assert sources["counts"]["legally_confirmed"] == 0
    assert datasets["human_verified_samples"] == 0
    assert benchmarks["truthfulness"]["development_only"] is True
    assert benchmarks["truthfulness"]["cost_usd"] is None
    assert benchmarks["agent_vs_pipeline"]["agent"]["total_tokens"] == 73100
    assert benchmarks["abstention"]["heldout_near_domain_specificity"] == 0.25
    assert runtime["active_document_count"] == 3
    assert runtime["active_chunk_count"] == 13
    assert runtime["counts"]["workflow_runs"] >= 0
    assert runtime["environment"] == "local_development"


def test_evidence_publisher_builds_portable_release_from_local_inputs(tmp_path: Path) -> None:
    def write_json(path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    content_hash = "a" * 64
    source = {
        "id": "source-one", "title": "Rule", "jurisdiction": "US",
        "publisher": "Regulator", "source_url": "https://official.test/rule",
        "format": "html", "source_language": "en", "ingestion_mode": "legal_document",
        "activation_policy": "manual_review",
    }
    write_json(tmp_path / "config/source_registry.json", {"sources": [source]})
    staged = tmp_path / "data/update-state/staged/source-one" / content_hash
    write_json(staged / "manifest.json", {
        "source_id": "source-one", "content_hash": content_hash,
        "created_at": "2026-07-22T00:00:00+00:00", "section_count": 1,
        "structural_review_status": "passed", "legal_review_status": "pending",
        "eligible_for_activation": True, "short_section_rate": 0.0,
        "quality_schema_version": "2.0", "temporal_review_status": "complete",
        "generic_heading_rate": 0.0, "blocking_reasons": [],
    })
    write_json(staged / "policy.json", {
        "title": "Rule", "publisher": "Regulator", "source_url": source["source_url"],
        "version": "v1", "retrieved_at": "2026-07-22",
        "sections": [{"section_id": "article-1", "heading": "Article 1", "text": "Truth."}],
    })
    write_json(tmp_path / "data/evaluation/set.json", {
        "name": "set", "label_status": "ai_reviewed_pending_human_verification",
        "ai_source_audit": {"human_verified_samples": 0},
        "samples": [{"query": "q", "jurisdiction": "US"}],
    })
    benchmarks = tmp_path / "data/benchmarks"
    write_json(benchmarks / "local-embedding-results.json", [{
        "model": "easy", "status": "ok", "sample_count": 1,
        "hit_rate_at_k": 1.0, "mean_reciprocal_rank": 1.0,
    }])
    write_json(benchmarks / "local-embedding-hard-v1.json", [])
    write_json(benchmarks / "local-rerank-results.json", [])
    write_json(benchmarks / "agent-vs-pipeline.json", {
        "pipeline": {"metrics": {"case_count": 1}},
        "agent": {"metrics": {"total_tokens": 5}, "cases": []},
    })
    write_json(benchmarks / "query-rewrite-hard-v1.json", {"model": "model"})
    write_json(benchmarks / "cross-language-abstention-v1.json", {
        "positive_count": 1, "negative_count": 1, "best": {"f1": 0.5},
    })
    write_json(benchmarks / "abstention-jina-hard-v1.json", {
        "heldout_threshold": 0.4, "near_domain_heldout_count": 1,
        "near_domain_specificity": 0.0,
    })
    write_json(benchmarks / "remediation-quality-v1.json", {"sample_count": 1})
    write_json(benchmarks / "pdf-quality.json", {"sample_count": 1})
    write_json(benchmarks / "external-smoke.json", {"checks": [{
        "name": "source:one", "status": "failed", "status_code": 403,
    }]})
    write_json(benchmarks / "portfolio-scale-v1.json", {
        "rag": {"sample_count": 120}, "workflow": {"case_count": 100},
    })
    write_json(benchmarks / "synthetic-pdf-scale-v1.json", {
        "document_count": 20, "page_count": 200,
        "data_type": "programmatically_generated_not_real_regulation",
    })

    database_path = tmp_path / "data/policyguard.db"
    connection = sqlite3.connect(database_path)
    for table in (
        "policy_scopes", "compliance_checks", "findings", "workflow_runs",
        "workflow_events", "agent_memories", "evaluation_reviews", "background_jobs",
        "audit_logs",
    ):
        connection.execute(f"CREATE TABLE {table} (id INTEGER)")
    connection.execute(
        "CREATE TABLE policy_documents (id TEXT, title TEXT, publisher TEXT, "
        "source_url TEXT, version TEXT, active INTEGER)"
    )
    connection.execute("CREATE TABLE policy_chunks (id TEXT, document_id TEXT)")
    connection.execute(
        "INSERT INTO policy_documents VALUES "
        "('doc', 'Rule', 'Regulator', 'https://official.test/rule', 'v1', 1)"
    )
    connection.execute("INSERT INTO policy_chunks VALUES ('chunk', 'doc')")
    connection.commit()
    connection.close()

    target = tmp_path / "release"
    inventory = publish_evidence_bundle(tmp_path, target)

    assert inventory["counts"]["latest_release_sections"] == 1
    assert validate_published_bundle(target) == []
    released = json.loads((target / "sources/source-one.json").read_text(encoding="utf-8"))
    assert released["capture"]["validation_errors"] == []
    assert released["document"]["sections"][0]["text"] == "Truth."
    result = json.loads((target / "benchmarks.json").read_text(encoding="utf-8"))
    assert result["external_smoke"]["checks"][0]["status"] == "blocked"
    assert result["portfolio_scale"]["workflow"]["case_count"] == 100
    assert result["synthetic_pdf_scale"]["page_count"] == 200

    release_path = target / "sources/source-one.json"
    lf_content = release_path.read_bytes().replace(b"\r\n", b"\n")
    release_path.write_bytes(lf_content.replace(b"\n", b"\r\n"))
    assert validate_published_bundle(target) == []
    assert inventory["sources"][0]["release_sha256"] == sha256(lf_content).hexdigest()
