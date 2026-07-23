import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

from policyguard.application.html_ingestion import parse_official_html
from policyguard.application.knowledge import load_policy_document


def clean_source_sections(source: dict, sections) -> list:
    source_id = source["id"]
    if source.get("ingestion_mode") == "source_catalog":
        return list(sections)
    cleaned = []
    started = source_id not in {"us-ftc-health-products-guidance", "us-ftc-can-spam"}
    for section in sections:
        text = " ".join(section.text.split())
        if source_id == "cn-samr-advertising-law":
            if not re.match(r"^第[一二三四五六七八九十百0-9]+[章条]", text):
                continue
            heading_match = re.match(r"^(第[一二三四五六七八九十百0-9]+[章条])", text)
            section = type(section)(section.section_id, heading_match.group(1), text)
        elif source_id == "us-ftc-health-products-guidance":
            started = started or section.heading.startswith("I. Preface")
            if not started or text in {"Tags:", "Advertising and Marketing", "Health Claims"}:
                continue
        elif source_id == "us-ftc-can-spam":
            started = started or text.startswith("Do you use email in your business?")
            if not started:
                continue
        if len(text) < 12 and not re.match(
            r"^(?:(?:[IVXLCDM]+|[A-Z])\.\s|第[一二三四五六七八九十百0-9]+[章条])",
            section.heading,
        ):
            continue
        cleaned.append(type(section)(section.section_id, section.heading, text))
    unique = []
    seen = set()
    for section in cleaned:
        key = (section.heading.casefold(), section.text.casefold())
        if key not in seen:
            seen.add(key)
            unique.append(section)
    return unique


def source_structure_quality(source: dict, sections: list) -> dict:
    texts = [section.text for section in sections]
    headings = [section.heading.strip() for section in sections]
    suspicious = sum(text.count("�") for text in texts)
    short = sum(len(text) < 30 for text in texts)
    eligible = source.get("ingestion_mode") == "legal_document"
    generic = sum(heading.casefold() == "official source update" for heading in headings)
    generic_rate = generic / max(len(headings), 1)
    temporal_values = (source.get("published_at"), source.get("effective_from"))
    temporal_complete = all(
        value and value not in {"undated", "1900-01-01"} for value in temporal_values
    )
    blocking_reasons = []
    if not eligible:
        blocking_reasons.append("not_a_legal_document")
    if len(sections) < 3:
        blocking_reasons.append("insufficient_sections")
    if suspicious:
        blocking_reasons.append("replacement_characters_present")
    if generic_rate >= 0.8:
        blocking_reasons.append("generic_heading_dominance")
    if not temporal_complete:
        blocking_reasons.append("temporal_metadata_missing")
    passed = not blocking_reasons
    return {
        "quality_schema_version": "2.0",
        "eligible_for_activation": eligible,
        "section_count": len(sections),
        "short_section_rate": round(short / max(len(sections), 1), 4),
        "unique_heading_count": len(set(headings)),
        "generic_heading_rate": round(generic_rate, 4),
        "replacement_character_count": suspicious,
        "temporal_review_status": "complete" if temporal_complete else "missing",
        "blocking_reasons": blocking_reasons,
        "structural_review_status": "passed" if passed else "blocked",
        "legal_review_status": "pending",
    }


def stage_source_snapshot(source: dict, state: dict, root: Path) -> dict:
    snapshot = Path(state["snapshot_path"])
    if not snapshot.is_file():
        raise FileNotFoundError("source_snapshot_missing")
    parsed_title, sections = parse_official_html(snapshot.read_bytes())
    sections = clean_source_sections(source, sections)
    title = source.get("title") or parsed_title
    if not sections:
        raise ValueError("source_snapshot_no_sections")
    content_hash = state["content_hash"]
    directory = root / source["id"] / content_hash
    directory.mkdir(parents=True, exist_ok=True)
    policy = {
        "title": title,
        "publisher": source["publisher"],
        "source_url": source["source_url"],
        "version": f"snapshot-{content_hash[:12]}",
        "published_at": source.get("published_at", "undated"),
        "retrieved_at": datetime.now(UTC).date().isoformat(),
        "scope_note": "Automatically parsed official HTML; requires structural and legal review.",
        "scopes": [{
            "jurisdiction": source["jurisdiction"],
            "category": (source.get("categories") or ["all"])[0],
            "channel": (source.get("channels") or ["all"])[0],
            "legal_level": source.get("legal_level", "regulatory_guidance"),
            "source_language": source.get("source_language", "en"),
            "translation_status": "original",
            "effective_from": source.get("effective_from", "1900-01-01"),
        }],
        "sections": [
            {"section_id": item.section_id, "heading": item.heading, "text": item.text}
            for item in sections
        ],
    }
    policy_path = directory / "policy.json"
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    quality = source_structure_quality(source, sections)
    manifest = {
        "source_id": source["id"],
        "ingestion_mode": source.get("ingestion_mode", "legal_document"),
        "content_hash": content_hash,
        "status": "staged",
        "revision": 0,
        "section_count": len(sections),
        **quality,
        "snapshot_path": str(snapshot),
        "diff_path": state.get("pending_diff_path"),
        "policy_path": str(policy_path),
        "created_at": datetime.now(UTC).isoformat(),
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def revise_staged_source_update(
    root: Path,
    source_id: str,
    content_hash: str,
    *,
    reviewer: str,
    expected_revision: int,
    published_at: str | None = None,
    effective_from: str | None = None,
    heading_overrides: dict[str, str] | None = None,
) -> dict:
    """Apply a reviewer's structural corrections without activating legal content."""
    directory = root / source_id / content_hash
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise LookupError("source_update_not_found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "staged":
        raise RuntimeError("source_update_not_staged")
    if manifest.get("ingestion_mode") is None:
        raise RuntimeError("source_update_restage_required")
    revision = int(manifest.get("revision", 0))
    if expected_revision != revision:
        raise RuntimeError("source_update_revision_conflict")
    if not any((published_at, effective_from, heading_overrides)):
        raise RuntimeError("source_update_no_corrections")
    for value in (published_at, effective_from):
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("source_update_invalid_date") from exc

    policy_path = Path(manifest["policy_path"])
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    changed_headings = []
    overrides = heading_overrides or {}
    sections_by_id = {item["section_id"]: item for item in policy["sections"]}
    unknown_ids = sorted(set(overrides) - set(sections_by_id))
    if unknown_ids:
        raise ValueError("source_update_unknown_section_id")
    for section_id, heading in overrides.items():
        cleaned = heading.strip()
        if not cleaned or len(cleaned) > 500:
            raise ValueError("source_update_invalid_heading")
        if sections_by_id[section_id]["heading"] != cleaned:
            sections_by_id[section_id]["heading"] = cleaned
            changed_headings.append(section_id)

    if published_at is not None:
        policy["published_at"] = published_at
    if effective_from is not None:
        for scope in policy.get("scopes", []):
            scope["effective_from"] = effective_from
    effective_date = next(
        (scope.get("effective_from") for scope in policy.get("scopes", []) if scope), None
    )
    source = {
        "id": source_id,
        "ingestion_mode": manifest["ingestion_mode"],
        "published_at": policy.get("published_at"),
        "effective_from": effective_date,
    }
    quality = source_structure_quality(source, [
        type("Section", (), {"text": item["text"], "heading": item["heading"]})()
        for item in policy["sections"]
    ])
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    history = list(manifest.get("structural_review_history", []))
    history.append({
        "revision": revision + 1,
        "reviewer": reviewer,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "published_at_changed": published_at is not None,
        "effective_from_changed": effective_from is not None,
        "changed_heading_count": len(changed_headings),
        "changed_heading_ids": changed_headings,
        "structural_review_status": quality["structural_review_status"],
        "blocking_reasons": quality["blocking_reasons"],
    })
    manifest.update({
        **quality,
        "revision": revision + 1,
        "structural_review_history": history,
        "legal_review_status": "pending",
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def list_staged_source_updates(root: Path, *, latest_only: bool = True) -> list[dict]:
    updates = []
    if not root.exists():
        return updates
    for path in root.glob("*/*/manifest.json"):
        updates.append(json.loads(path.read_text(encoding="utf-8")))
    updates = sorted(updates, key=lambda item: item["created_at"], reverse=True)
    if not latest_only:
        return updates
    latest = {}
    for item in updates:
        latest.setdefault(item["source_id"], item)
    return list(latest.values())


def approve_source_update(
    root: Path,
    source_id: str,
    content_hash: str,
    reviewer: str,
    repository,
    *,
    legal_review_confirmed: bool,
):
    directory = root / source_id / content_hash
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise LookupError("source_update_not_found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("quality_schema_version") != "2.0":
        raise RuntimeError("source_update_quality_gate_outdated")
    if manifest.get("structural_review_status") != "passed":
        raise RuntimeError("source_update_structure_not_approved")
    if manifest.get("temporal_review_status") != "complete":
        raise RuntimeError("source_update_temporal_review_required")
    if not legal_review_confirmed:
        raise RuntimeError("source_update_legal_review_required")
    document = load_policy_document(Path(manifest["policy_path"]))
    repository.activate_document_version(document)
    manifest.update({
        "status": "active", "reviewer": reviewer,
        "legal_review_status": "confirmed",
        "approved_at": datetime.now(UTC).isoformat(), "document_id": document.id,
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
