import json
import re
from datetime import UTC, datetime
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
    suspicious = sum(text.count("�") for text in texts)
    short = sum(len(text) < 30 for text in texts)
    eligible = source.get("ingestion_mode") == "legal_document"
    passed = eligible and len(sections) >= 3 and suspicious == 0
    return {
        "eligible_for_activation": eligible,
        "section_count": len(sections),
        "short_section_rate": round(short / max(len(sections), 1), 4),
        "replacement_character_count": suspicious,
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
        "content_hash": content_hash,
        "status": "staged",
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
    if manifest.get("structural_review_status") != "passed":
        raise RuntimeError("source_update_structure_not_approved")
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
