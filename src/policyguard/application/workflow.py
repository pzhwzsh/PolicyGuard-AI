from datetime import UTC, datetime
from uuid import uuid4

from policyguard.application.evidence_support import EvidenceVerifier
from policyguard.application.guardrails import default_guardrail_policy
from policyguard.application.knowledge import BM25Retriever
from policyguard.application.llm import BaselineClaimExtractor, ClaimExtractor
from policyguard.application.ports import KnowledgeRepository, WorkflowRepository
from policyguard.application.query_rewrite import (
    JsonQueryRewriteCache,
    cached_rewrite_batch,
    is_cross_language_query,
    multi_query_search,
    should_rewrite,
)
from policyguard.domain.models import KnowledgeFilter
from policyguard.domain.workflow import WorkflowEvent, WorkflowRun, WorkflowStatus


class ComplianceWorkflowService:
    """Explicit V0 workflow; future LangGraph nodes can replace individual steps."""

    def __init__(
        self,
        knowledge_repository: KnowledgeRepository,
        workflow_repository: WorkflowRepository,
        claim_extractor: ClaimExtractor | None = None,
        retriever=None,
        evidence_verifier: EvidenceVerifier | None = None,
        query_rewriter=None,
        query_rewrite_cache: JsonQueryRewriteCache | None = None,
    ) -> None:
        self.retriever = retriever or BM25Retriever(knowledge_repository)
        self.workflow_repository = workflow_repository
        self.claim_extractor = claim_extractor
        self.evidence_verifier = evidence_verifier
        self.query_rewriter = query_rewriter
        self.query_rewrite_cache = query_rewrite_cache

    def execute(
        self,
        *,
        product: dict,
        markets: list[str],
        category: str,
        channel: str,
        as_of: str | None,
    ) -> WorkflowRun:
        run = WorkflowRun(
            id=str(uuid4()),
            status=WorkflowStatus.RUNNING,
            current_step="validate_input",
            input_payload={
                "product": product,
                "markets": markets,
                "category": category,
                "channel": channel,
                "as_of": as_of,
            },
        )
        try:
            default_guardrail_policy().validate_jurisdictions(markets)
            self._event(run, "validate_input", "completed", {"market_count": len(markets)})
            baseline_extractor = BaselineClaimExtractor()
            extractor = self.claim_extractor or baseline_extractor
            try:
                extracted_claims = extractor.extract(
                    title=product.get("title", ""), description=product.get("description", "")
                )
                extraction_step = (
                    "claim_extraction_llm"
                    if extractor is not baseline_extractor
                    else "claim_extraction_baseline"
                )
                self._event(
                    run,
                    extraction_step,
                    "completed",
                    {
                        "provider": extractor.provider_name,
                        "model": getattr(extractor, "last_model", None)
                        or extractor.model_name,
                        "claim_count": len(extracted_claims),
                    },
                )
            except Exception as exc:
                extracted_claims = baseline_extractor.extract(
                    title=product.get("title", ""), description=product.get("description", "")
                )
                self._event(
                    run,
                    "claim_extraction_fallback",
                    "degraded",
                    {"error": type(exc).__name__, "fallback": baseline_extractor.provider_name},
                )
            claims = " ".join(str(item["text"]) for item in extracted_claims)
            self._event(
                run,
                "claim_normalization",
                "completed",
                {"claim_length": len(claims), "claim_count": len(extracted_claims)},
            )
            scopes = {}
            market_hits = {}
            for market in markets:
                scope = KnowledgeFilter(
                    jurisdiction=market.upper(),
                    category=category,
                    channel=channel,
                    as_of=as_of,
                )
                hits = self.retriever.search(claims, top_k=5, scope=scope)
                retrieval_failures = getattr(self.retriever, "last_failures", [])
                if retrieval_failures:
                    self._event(
                        run,
                        "retrieval_fallback",
                        "degraded",
                        {
                            "market": scope.jurisdiction,
                            "failures": retrieval_failures,
                            "selected": "next_available_retriever",
                        },
                    )
                scopes[scope.jurisdiction] = scope
                market_hits[scope.jurisdiction] = hits
            rewrite_items = []
            rewrite_reasons = {}
            if self.query_rewriter and self.query_rewrite_cache:
                for index, market in enumerate(markets):
                    market_id = market.upper()
                    hits = market_hits[market_id]
                    top_score = hits[0].score if hits else None
                    if should_rewrite(claims, market_id, top_score):
                        rewrite_reasons[market_id] = {
                            "language_mismatch": is_cross_language_query(claims, market_id),
                            "original_language_preserved": True,
                            "target_source_language": "zh" if market_id == "CN" else "en",
                            "weak_top_score": top_score is not None and top_score < 0.45,
                            "original_top_score": top_score,
                        }
                        rewrite_items.append(
                            {
                                "query_id": f"market-{index}",
                                "query": claims,
                                "jurisdiction": market_id,
                            }
                        )
            if rewrite_items:
                try:
                    rewrites, rewrite_usage = cached_rewrite_batch(
                        self.query_rewriter, self.query_rewrite_cache, rewrite_items
                    )
                    for rewrite in rewrites:
                        market_id = rewrite.jurisdiction.upper()
                        added_constraints = rewrite.added_constraints()
                        if added_constraints:
                            self._event(
                                run,
                                "query_rewrite_drift",
                                "rejected",
                                {
                                    "market": market_id,
                                    "added_constraints": added_constraints,
                                    "fallback": "original_query",
                                },
                            )
                            continue
                        market_hits[market_id] = multi_query_search(
                            self.retriever, rewrite, 5, scopes[market_id]
                        )
                    self._event(
                        run,
                        "query_rewrite",
                        "completed",
                        {
                            "rewritten_market_count": len(rewrites),
                            "model": rewrite_usage.get(
                                "selected_model", self.query_rewriter.model
                            ),
                            "fallback_used": rewrite_usage.get("fallback_used", False),
                            "total_tokens": rewrite_usage.get("total_tokens", 0),
                            "cache_hits": rewrite_usage.get("cache_hits", 0),
                            "cache_misses": rewrite_usage.get("cache_misses", 0),
                            "retrieval_strategy": "original_plus_rewrites_rrf",
                            "reasons": rewrite_reasons,
                        },
                    )
                except Exception as exc:
                    self._event(
                        run,
                        "query_rewrite_fallback",
                        "degraded",
                        {"error": type(exc).__name__, "fallback": "original_query"},
                    )
            market_results = []
            for market in markets:
                scope = scopes[market.upper()]
                hits = market_hits[market.upper()]
                market_results.append(
                    {
                        "market": scope.jurisdiction,
                        "candidate_evidence": [
                            {
                                "rank": hit.rank,
                                "score": hit.score,
                                "section_id": hit.chunk.section_id,
                                "document_id": hit.chunk.document_id,
                                "heading": hit.chunk.heading,
                                "text": hit.chunk.text,
                                "source_url": hit.chunk.source_url,
                            }
                            for hit in hits
                        ],
                    }
                )
                self._event(
                    run,
                    "collect_evidence",
                    "completed",
                    {"market": scope.jurisdiction, "hit_count": len(hits)},
                )
            has_evidence = any(item["candidate_evidence"] for item in market_results)
            evidence_supported = None
            evidence_usage = {}
            if self.evidence_verifier and has_evidence:
                cases = [
                    {
                        "case_id": item["market"],
                        "query": claims,
                        "evidence": "\n\n".join(
                            f"[{evidence['section_id']}] {evidence['heading']}\n{evidence['text']}"
                            for evidence in item["candidate_evidence"][:3]
                        ),
                    }
                    for item in market_results
                ]
                try:
                    decisions, evidence_usage = self.evidence_verifier.verify_batch(cases)
                    by_market = {decision.case_id: decision for decision in decisions}
                    for item in market_results:
                        decision = by_market.get(item["market"])
                        item["evidence_support"] = (
                            {
                                "supported": decision.supported,
                                "quote": decision.quote,
                                "quote_valid": decision.quote_valid,
                                "reason": decision.reason,
                            }
                            if decision
                            else {"supported": False, "error": "decision_missing"}
                        )
                    evidence_supported = any(
                        item["evidence_support"].get("supported", False) for item in market_results
                    )
                    self._event(
                        run,
                        "evidence_support_llm",
                        "completed",
                        {
                            "provider": "openai_compatible",
                            "decision_count": len(decisions),
                            "supported_market_count": sum(
                                item["evidence_support"].get("supported", False)
                                for item in market_results
                            ),
                            "total_tokens": evidence_usage.get("total_tokens"),
                            "model": evidence_usage.get("selected_model"),
                            "fallback_used": evidence_usage.get("fallback_used", False),
                        },
                    )
                except Exception as exc:
                    evidence_supported = None
                    self._event(
                        run,
                        "evidence_support_fallback",
                        "degraded",
                        {"error": type(exc).__name__, "fallback": "human_review"},
                    )
            run.result_payload = {
                "evidence_only": True,
                "claims": extracted_claims,
                "markets": market_results,
                "evidence_supported": evidence_supported,
                "evidence_usage": evidence_usage,
                "note": "Candidate evidence only; no legal conclusion or automatic mutation.",
            }
            run.status = (
                WorkflowStatus.NEEDS_MORE_EVIDENCE
                if not has_evidence or evidence_supported is False
                else WorkflowStatus.REVIEW_REQUIRED
            )
            run.current_step = "human_review_route"
            self._event(
                run,
                "human_review_route",
                run.status.value,
                {"reason": "evidence_requires_human_interpretation"},
            )
        except Exception as exc:  # Persist failure state so the run is inspectable.
            run.status = WorkflowStatus.FAILED
            run.current_step = "failed"
            run.result_payload = {"error": type(exc).__name__, "message": str(exc)}
            self._event(run, "failed", "failed", {"error": type(exc).__name__})
        return self.workflow_repository.save(run)

    def review(
        self,
        *,
        run_id: str,
        decision_id: str,
        decision: str,
        reviewer: str,
        comment: str,
    ) -> WorkflowRun:
        run = self.workflow_repository.get(run_id)
        if run is None:
            raise LookupError("workflow_not_found")
        existing = run.result_payload.get("review")
        if isinstance(existing, dict) and existing.get("decision_id") == decision_id:
            return run
        terminal = {WorkflowStatus.REVIEW_ACCEPTED, WorkflowStatus.REVIEW_REJECTED}
        if run.status in terminal:
            raise RuntimeError("workflow_review_conflict")
        if run.status not in {
            WorkflowStatus.REVIEW_REQUIRED,
            WorkflowStatus.NEEDS_MORE_EVIDENCE,
        }:
            raise RuntimeError("workflow_not_reviewable")
        target_status = (
            WorkflowStatus.REVIEW_ACCEPTED
            if decision == "accept"
            else WorkflowStatus.REVIEW_REJECTED
        )
        run.status = target_status
        run.current_step = "review_completed"
        run.result_payload["review"] = {
            "decision_id": decision_id,
            "decision": decision,
            "reviewer": reviewer,
            "comment": comment,
            "decided_at": datetime.now(UTC).isoformat(),
        }
        self._event(
            run,
            "human_review_decision",
            target_status.value,
            {
                "decision_id": decision_id,
                "decision": decision,
                "reviewer": reviewer,
            },
        )
        return self.workflow_repository.save(run)

    def _event(self, run: WorkflowRun, step: str, status: str, detail: dict) -> None:
        run.current_step = step
        run.updated_at = datetime.now(UTC)
        run.events.append(
            WorkflowEvent(
                sequence=len(run.events) + 1,
                step=step,
                status=status,
                detail=detail,
                created_at=run.updated_at,
            )
        )
