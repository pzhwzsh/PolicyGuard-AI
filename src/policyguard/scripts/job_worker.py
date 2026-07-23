import argparse
import json
from pathlib import Path
from time import sleep

import httpx

from policyguard.application.batch_review import process_batch_review
from policyguard.application.cellar import CellarClient
from policyguard.application.creative_image_provider import ProductSceneEditProvider
from policyguard.application.creative_studio import CreativeStudioWorkspace
from policyguard.application.document_ingestion import (
    configured_document_router,
    stage_parsed_document,
)
from policyguard.application.embeddings import (
    configured_embedding_chain,
    index_missing_embeddings,
)
from policyguard.application.evaluation_workbench import run_retrieval_evaluation
from policyguard.application.jobs import PersistentJobQueue
from policyguard.application.media_ingestion import parse_media_with_sidecar, stage_media_result
from policyguard.application.provider_http import is_transient_provider_error
from policyguard.application.source_monitor import OfficialSourceMonitor
from policyguard.application.source_registry import load_source_registry
from policyguard.application.source_updates import stage_source_snapshot
from policyguard.config import get_settings
from policyguard.infrastructure.database import Database, ResourceOwnershipRecord
from policyguard.infrastructure.repositories import SqlAlchemyKnowledgeRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--max-jobs", type=int, default=0)
    return parser.parse_args()


def handle_parse_document(payload: dict, settings) -> dict:
    upload_root = Path(settings.upload_dir).resolve()
    path = Path(payload["path"]).resolve()
    inbox = (upload_root / "inbox").resolve()
    if inbox not in path.parents or not path.is_file():
        raise ValueError("job_document_path_invalid")
    content = path.read_bytes()
    parsed, route = configured_document_router(settings).parse(content, payload["filename"])
    document_id, chunks = stage_parsed_document(parsed, route, upload_root)
    original = upload_root / document_id / "original.pdf"
    if not original.exists():
        path.replace(original)
    return {
        "document_id": document_id,
        "status": parsed.status,
        "parser_route": route["route"],
        "chunk_count": len(chunks),
    }


def handle_source_monitor(settings) -> dict:
    root = Path(__file__).parents[3]
    all_sources = load_source_registry(root / "config/source_registry.json")
    sources = [source for source in all_sources if not source.get("cellar_celex")]
    with httpx.Client(
        timeout=45, follow_redirects=True,
        headers={"User-Agent": "PolicyGuard-AI source monitor/0.1 (+local research)"},
    ) as client:
        results = OfficialSourceMonitor(
            client,
            root / "data/update-state/source-state.json",
            root / "data/update-state/source-history.jsonl",
            root / "data/update-state/snapshots",
        ).check(sources)
    state = json.loads(
        (root / "data/update-state/source-state.json").read_text(encoding="utf-8")
    )
    staged = []
    for source in sources:
        result = next(item for item in results if item.source_url == source["source_url"])
        if result.status not in {"baseline", "changed"}:
            continue
        staged.append(stage_source_snapshot(
            source, state[source["source_url"]], root / "data/update-state/staged"
        ))
    cellar_state_path = root / "data/update-state/cellar-state.json"
    for source in (item for item in all_sources if item.get("cellar_celex")):
        try:
            document = CellarClient(client).fetch_xhtml(source["cellar_celex"])
            cellar_result = OfficialSourceMonitor(
                httpx.Client(transport=httpx.MockTransport(
                    lambda request, d=document: httpx.Response(200, content=d.content)
                )),
                cellar_state_path,
                root / "data/update-state/cellar-history.jsonl",
                root / "data/update-state/snapshots",
            ).check([source])[0]
            cellar_state = json.loads(cellar_state_path.read_text(encoding="utf-8"))
            if cellar_result.status in {"baseline", "changed"}:
                staged.append(stage_source_snapshot(
                    source,
                    cellar_state[source["source_url"]],
                    root / "data/update-state/staged",
                ))
            results.append(cellar_result)
        except Exception as exc:
            results.append(type("CellarFailure", (), {
                "status": "failed", "source_url": source["source_url"],
                "error": f"{type(exc).__name__}: {exc}",
            })())
    return {
        "statuses": {
            status: sum(item.status == status for item in results)
            for status in {"baseline", "changed", "unchanged", "failed"}
        },
        "staged_source_ids": [item["source_id"] for item in staged],
    }


def handle_reindex_embeddings(settings, session) -> dict:
    providers, failures = configured_embedding_chain(settings)
    if not providers:
        raise RuntimeError("embedding_not_configured")
    provider = providers[0]
    count = index_missing_embeddings(SqlAlchemyKnowledgeRepository(session), provider)
    return {
        "indexed_chunks": count,
        "provider": provider.provider_name,
        "model": provider.model_name,
        "initialization_failures": failures,
    }


def handle_batch_review(settings, session, payload: dict) -> dict:
    upload_root = Path(settings.upload_dir).resolve()
    input_path = Path(payload["path"]).resolve()
    batch_root = (upload_root / "batches").resolve()
    table_root = (upload_root / "tables").resolve()
    regular_batch = batch_root in input_path.parents
    cleaned_table = (
        payload.get("cleaning_revision") is not None
        and input_path.parent == (table_root / str(payload["batch_id"])).resolve()
        and input_path.name == "cleaned-input.csv"
    )
    if not (regular_batch or cleaned_table) or not input_path.is_file():
        raise ValueError("batch_path_invalid")
    output_dir = batch_root / payload["batch_id"]
    return process_batch_review(session, input_path, output_dir)


def handle_model_evaluation(session, payload: dict) -> dict:
    return run_retrieval_evaluation(
        session,
        root=Path(__file__).parents[3],
        dataset=payload["dataset"],
        candidates=payload["candidates"],
        top_k=payload["top_k"],
        thresholds=payload["thresholds"],
    )


def handle_media_claim_extraction(settings, payload: dict) -> dict:
    upload_root = Path(settings.upload_dir).resolve()
    path = Path(payload["path"]).resolve()
    inbox = (upload_root / "media" / "inbox").resolve()
    if inbox not in path.parents or not path.is_file():
        raise ValueError("media_path_invalid")
    result = parse_media_with_sidecar(
        path,
        base_url=settings.rapidocr_base_url,
        api_key=settings.document_parser_api_key,
    )
    return stage_media_result(upload_root, path, result)


def handle_creative_scene_generation(settings, payload: dict) -> dict:
    workspace = CreativeStudioWorkspace(Path(settings.upload_dir))
    manifest = workspace.load(payload["project_id"])
    if manifest["revision"] != payload["expected_revision"]:
        raise ValueError("creative_project_revision_conflict")
    source_name = manifest.get("source_image")
    if not source_name:
        raise ValueError("creative_source_image_required")
    source_path = workspace.directory(payload["project_id"]) / source_name
    if (
        not source_path.is_file()
        or source_path.parent != workspace.directory(payload["project_id"])
    ):
        raise ValueError("creative_source_image_invalid")
    request = manifest["scene_request"]
    spec = manifest["platform_spec"]
    content, metadata = ProductSceneEditProvider(
        base_url=settings.image_generation_base_url,
        api_key=settings.image_generation_api_key,
        model=settings.image_generation_model,
        timeout_seconds=settings.image_generation_timeout_seconds,
    ).edit(
        source=source_path.read_bytes(),
        prompt=request["prompt"],
        negative_prompt=request["negative_prompt"],
        width=spec["width"],
        height=spec["height"],
    )
    return workspace.add_scene_candidate(
        payload["project_id"],
        content=content,
        expected_revision=payload["expected_revision"],
        provider_metadata=metadata,
    )


def main() -> None:
    args = parse_args()
    settings = get_settings()
    database = Database(settings.database_url)
    database.initialize()
    handled = 0
    while args.max_jobs <= 0 or handled < args.max_jobs:
        with database.session_factory() as session:
            queue = PersistentJobQueue(session)
            queue.requeue_stale()
            job = queue.claim({
                "parse_document", "source_monitor", "reindex_embeddings",
                "batch_compliance_review", "model_evaluation",
                "media_claim_extraction",
                "creative_scene_generation",
            })
            if job is None:
                if args.max_jobs:
                    break
                sleep(max(args.poll_seconds, 0.1))
                continue
            try:
                if job.job_type == "parse_document":
                    result = handle_parse_document(job.payload, settings)
                    tenant_id = job.payload.get("tenant_id")
                    ownership_key = ("document", result["document_id"], tenant_id)
                    if tenant_id and session.get(ResourceOwnershipRecord, ownership_key) is None:
                        session.add(ResourceOwnershipRecord(
                            resource_type="document",
                            resource_id=result["document_id"],
                            tenant_id=tenant_id,
                        ))
                        session.commit()
                elif job.job_type == "source_monitor":
                    result = handle_source_monitor(settings)
                elif job.job_type == "batch_compliance_review":
                    result = handle_batch_review(settings, session, job.payload)
                elif job.job_type == "model_evaluation":
                    result = handle_model_evaluation(session, job.payload)
                elif job.job_type == "media_claim_extraction":
                    result = handle_media_claim_extraction(settings, job.payload)
                    tenant_id = job.payload.get("tenant_id")
                    ownership_key = ("media", result["media_id"], tenant_id)
                    if tenant_id and session.get(ResourceOwnershipRecord, ownership_key) is None:
                        session.add(ResourceOwnershipRecord(
                            resource_type="media",
                            resource_id=result["media_id"],
                            tenant_id=tenant_id,
                        ))
                        session.commit()
                elif job.job_type == "creative_scene_generation":
                    result = handle_creative_scene_generation(settings, job.payload)
                else:
                    result = handle_reindex_embeddings(settings, session)
                queue.complete(job.id, result)
            except Exception as exc:
                retryable = not isinstance(exc, ValueError)
                if isinstance(exc, httpx.HTTPStatusError):
                    retryable = is_transient_provider_error(exc)
                queue.fail(
                    job.id,
                    f"{type(exc).__name__}: {exc}",
                    retryable=retryable,
                )
        handled += 1


if __name__ == "__main__":
    main()
