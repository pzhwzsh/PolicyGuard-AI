from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from policyguard.application.embeddings import (
    DenseRetriever,
    configured_embedding_chain,
)
from policyguard.application.hybrid import FallbackRetriever, HybridRetriever
from policyguard.application.knowledge import BM25Retriever, ingest_source_directory
from policyguard.application.remediation import RemediationService
from policyguard.application.rerank import RerankedHybridRetriever, configured_reranker
from policyguard.application.tools import SuggestConservativeRewriteTool, ToolRegistry
from policyguard.config import get_settings
from policyguard.domain.models import KnowledgeFilter
from policyguard.infrastructure.database import Database
from policyguard.infrastructure.repositories import (
    SqlAlchemyKnowledgeRepository,
    SqlAlchemyWorkflowRepository,
)

mcp = FastMCP("PolicyGuard AI")


@contextmanager
def _session():
    settings = get_settings()
    database = Database(settings.database_url)
    database.initialize()
    with database.session_factory() as session:
        ingest_source_directory(SqlAlchemyKnowledgeRepository(session), Path(settings.source_dir))
        yield settings, session
    database.engine.dispose()


def _hit_payload(hit) -> dict[str, Any]:
    return {
        "rank": hit.rank,
        "score": hit.score,
        "chunk_id": hit.chunk.id,
        "document_title": hit.chunk.document_title,
        "jurisdiction": hit.chunk.jurisdiction,
        "section_id": hit.chunk.section_id,
        "heading": hit.chunk.heading,
        "text": hit.chunk.text,
        "source_url": hit.chunk.source_url,
    }


@mcp.tool()
def search_policy(
    query: str,
    market: str = "CN",
    category: str = "all",
    channel: str = "all",
    top_k: int = 5,
) -> dict[str, Any]:
    """Read-only search of official policy evidence for a market and scope."""
    if not query.strip():
        raise ValueError("query_must_not_be_empty")
    if not 1 <= top_k <= 20:
        raise ValueError("top_k_out_of_range")
    with _session() as (settings, session):
        repository = SqlAlchemyKnowledgeRepository(session)
        scope = KnowledgeFilter(market.upper(), category, channel)
        providers, initialization_failures = configured_embedding_chain(settings)
        if not providers and not initialization_failures:
            hits = BM25Retriever(repository).search(query, top_k=top_k, scope=scope)
            retriever = "lexical_bm25_cjk_v1"
        else:
            reranker = configured_reranker(settings)
            if reranker is not None and providers:
                primary = RerankedHybridRetriever(
                    repository,
                    HybridRetriever(repository, DenseRetriever(repository, providers[0])),
                    reranker,
                )
                retriever = "hybrid_rrf_rerank"
                remaining = providers[1:]
            elif providers:
                primary = HybridRetriever(
                    repository, DenseRetriever(repository, providers[0])
                )
                retriever = "hybrid_rrf"
                remaining = providers[1:]
            else:
                primary = BM25Retriever(repository)
                retriever = "lexical_bm25_cjk_v1"
                remaining = []
            candidates = [
                primary,
                *(
                    HybridRetriever(repository, DenseRetriever(repository, provider))
                    for provider in remaining
                ),
            ]
            candidates.append(BM25Retriever(repository))
            fallback = FallbackRetriever(
                *candidates,
                initial_failures=initialization_failures,
            )
            hits = fallback.search(query, top_k=top_k, scope=scope)
            if fallback.last_failures:
                retriever += "_fallback"
        return {
            "query": query,
            "market": scope.jurisdiction,
            "category": category,
            "channel": channel,
            "retriever": retriever,
            "evidence_only": True,
            "results": [_hit_payload(hit) for hit in hits],
        }


@mcp.tool()
def get_workflow(run_id: str) -> dict[str, Any]:
    """Read-only lookup of a compliance workflow and its event checkpoint."""
    with _session() as (_, session):
        run = SqlAlchemyWorkflowRepository(session).get(run_id)
        if run is None:
            raise ValueError("workflow_not_found")
        return {
            "id": run.id,
            "status": run.status.value,
            "current_step": run.current_step,
            "input_payload": run.input_payload,
            "result_payload": run.result_payload,
            "events": [
                {
                    "sequence": event.sequence,
                    "step": event.step,
                    "status": event.status,
                    "detail": event.detail,
                    "created_at": event.created_at.isoformat(),
                }
                for event in run.events
            ],
        }


@mcp.tool()
def create_remediation_plan(run_id: str, plan_id: str) -> dict[str, Any]:
    """Create an internal, non-mutating remediation plan after human acceptance."""
    with _session() as (_, session):
        service = RemediationService(
            SqlAlchemyWorkflowRepository(session),
            ToolRegistry([SuggestConservativeRewriteTool()]),
        )
        run = service.plan(run_id=run_id, plan_id=plan_id)
        return get_workflow(run.id)


@mcp.tool()
def create_internal_draft(run_id: str, execution_id: str, approved_by: str) -> dict[str, Any]:
    """Create an internal draft after a separate approval; never publishes externally."""
    with _session() as (_, session):
        service = RemediationService(
            SqlAlchemyWorkflowRepository(session),
            ToolRegistry([SuggestConservativeRewriteTool()]),
        )
        run = service.create_draft(
            run_id=run_id, execution_id=execution_id, approved_by=approved_by
        )
        return get_workflow(run.id)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
