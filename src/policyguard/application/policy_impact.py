import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from policyguard.application.jobs import PersistentJobQueue
from policyguard.infrastructure.database import (
    AgentMemoryRecord,
    PolicyDocumentRecord,
    WorkflowRunRecord,
)


def _sections(payload: dict) -> dict[str, dict]:
    return {
        str(item["section_id"]): item
        for item in payload.get("sections", [])
        if item.get("section_id")
    }


def section_changes(old_sections: dict[str, dict], new_sections: dict[str, dict]) -> list[dict]:
    changes = []
    for section_id in sorted(old_sections.keys() | new_sections.keys()):
        old = old_sections.get(section_id)
        new = new_sections.get(section_id)
        if old is None:
            change_type = "added"
        elif new is None:
            change_type = "removed"
        elif (old.get("heading"), old.get("text")) != (new.get("heading"), new.get("text")):
            change_type = "modified"
        else:
            continue
        changes.append({
            "section_id": section_id,
            "change_type": change_type,
            "old_heading": old.get("heading") if old else None,
            "new_heading": new.get("heading") if new else None,
        })
    return changes


def analyze_policy_impact(
    session: Session,
    staged_root: Path,
    source_id: str,
    content_hash: str,
) -> dict:
    directory = staged_root / source_id / content_hash
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise LookupError("source_update_not_found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    new_policy = json.loads(Path(manifest["policy_path"]).read_text(encoding="utf-8"))
    source_url = new_policy["source_url"]
    active = session.scalar(
        select(PolicyDocumentRecord)
        .where(
            PolicyDocumentRecord.source_url == source_url,
            PolicyDocumentRecord.active.is_(True),
        )
        .options(selectinload(PolicyDocumentRecord.chunks))
    )
    old_sections = {
        item.section_id: {
            "section_id": item.section_id,
            "heading": item.heading,
            "text": item.text,
        }
        for item in (active.chunks if active else [])
    }
    changes = section_changes(old_sections, _sections(new_policy))
    changed_section_ids = {item["section_id"] for item in changes}

    affected_workflows = []
    for run in session.scalars(select(WorkflowRunRecord)).all():
        evidence = [
            item
            for market in (run.result_payload or {}).get("markets", [])
            for item in market.get("candidate_evidence", [])
        ]
        matched = sorted({
            item.get("section_id")
            for item in evidence
            if item.get("source_url") == source_url
            and item.get("section_id") in changed_section_ids
        })
        if matched:
            affected_workflows.append({"workflow_id": run.id, "section_ids": matched})

    affected_memories = []
    if active is not None:
        for memory in session.scalars(select(AgentMemoryRecord)).all():
            if (memory.source_versions or {}).get(source_url) == active.version:
                affected_memories.append(memory.id)

    queue = PersistentJobQueue(session)
    jobs = []
    for item in affected_workflows:
        job = queue.enqueue(
            "policy_re_review",
            {
                "source_id": source_id,
                "content_hash": content_hash,
                "source_url": source_url,
                "old_document_id": active.id if active else None,
                "old_version": active.version if active else None,
                **item,
            },
            idempotency_key=f"policy-impact:{source_id}:{content_hash}:{item['workflow_id']}",
            max_attempts=5,
        )
        jobs.append(job.id)

    return {
        "source_id": source_id,
        "content_hash": content_hash,
        "source_url": source_url,
        "old_document_id": active.id if active else None,
        "old_version": active.version if active else None,
        "changes": changes,
        "changed_section_count": len(changes),
        "affected_workflows": affected_workflows,
        "affected_workflow_count": len(affected_workflows),
        "affected_memory_ids": affected_memories,
        "affected_memory_count": len(affected_memories),
        "re_review_job_ids": jobs,
        "automatic_activation": False,
    }
