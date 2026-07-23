# Changelog

All notable changes to PolicyGuard AI are documented here. The format follows Keep a Changelog,
and versions follow Semantic Versioning.

## [Unreleased]

### Changed

- Add a live reliability and cost view with provider/job retry rates, fallback rate, queue age,
  categorized failures, per-model token totals, price-based estimates, and threshold alerts.
- Add an asynchronous retrieval-model A/B workbench with allowlisted candidates, dataset hashes,
  quality/latency thresholds, local-cost accounting, and human-gated promotion records.
- Add exportable batch evidence packages plus revision-protected staged deletion, deletion
  tombstones, and a dry-run retention inventory for CSV/XLSX inputs and review results.
- Add per-workflow model-call/token budgets, separate admin/reviewer keys, tenant-owned resources,
  failure-injection tests, and a PostgreSQL API/worker production Compose stack.
- Add bounded image/video OCR jobs with frame timestamps and a 30-frame cap, plus preview-only
  RPA and DingTalk handoffs that never execute or send automatically.
- Add bounded transient provider retries, primary-to-backup model routing, runtime retrieval
  degradation to BM25, and reranker degradation to the original hybrid candidate ranking.
- Accept confirmed spreadsheet-cleaning inputs in the batch worker instead of repeatedly rejecting
  the `uploads/tables` path, while retaining workspace path-containment checks.
- Verify every local backup after creation and add dry-run-first restore with pre-restore safety
  copies and optional timestamp-directory retention.
- Route CSV/XLSX uploads through a staged cleaning preview and explicit human confirmation before
  batch review; deterministic normalization makes zero model calls and preserves source provenance.
- Keep Jina embeddings as the default after a fresh difficult-set comparison against BGE small and
  multilingual MiniLM; BGE is faster but loses material retrieval accuracy on the governed set.
- Block legacy staged sources until they pass quality-schema-v2 heading and temporal checks; source
  snapshots remain publishable as provenance evidence but are not activation candidates.
- Treat template-expanded RAG data as a synthetic stress suite rather than legal holdout evidence,
  and map generic `100%安全` remediation samples to Article 4 truthfulness instead of Article 9.
- Separate incomplete provider runs from model-quality failures in evidence-support benchmarks;
  recall and specificity are invalid when decision coverage is incomplete.
- Mark retrieved citations as unverified candidates and require authenticated reviewer identity for
  workflow decisions and draft approvals when admin authentication is enabled.
- Add versioned reviewer corrections for blocked source headings and temporal metadata, with
  optimistic-concurrency checks and immutable structural-review history.
- Expose blocked-source corrections in the management UI so reviewers can submit verified dates
  and section-heading mappings before the separate legal approval step.
- Add authenticated, revision-checked deletion of staged uploads and derived artifacts, plus a
  dry-run-first retention cleanup command that never removes activated knowledge documents.
- Replace fixed 3,000-character PDF splitting with structure-preserving, CJK-aware token windows
  capped at 800 approximate tokens with 100-token overlap and explicit chunk provenance metadata.

- Split the customer review page from the management console. The public workflow no longer exposes
  policy ingestion, source activation, evaluation queues, model status, job controls, or execution
  traces; those controls remain under `/admin`.
- Reorganized the internal console into focused panels and added evidence-backed P50/P95/P99,
  throughput, success-rate, serial-workflow, and PDF benchmark summaries.
- Workflow checkpoints now append immutable events instead of deleting and reinserting the full
  event history on every save.

- Rebuilt the handoff and development guides as UTF-8, removed stale and contradictory project
  states, and defined one source of truth for change history, current status, remaining work, data
  evidence, and architecture decisions.
- Removed repeated deployment-status wording from the internal handoff.
- Removed audience-specific positioning language from engineering documentation.

### Added

- Multi-Sheet spreadsheet header detection, editable canonical field mappings, row-level validation,
  formula-safe exports, partial-valid-row submission, and downloadable cleaning reports.
- Independent holdout auditing with minimum-size, freeze-state, duplicate, review-completion, and
  development-leakage gates plus deterministic retrieval failure classification.
- Real-PDF provenance and layout coverage audits, an official PDF candidate registry, and a strict
  downloader that rejects WAF/HTML responses rather than counting them as documents.
- PostgreSQL benchmark matrices for 1/4/8 workers and explicit queue idempotency, unique-claim, and
  stale-worker recovery evidence.
- LLM quality/latency/token/cost reports with P50/P95/P99 and relay-specific price requirements.
- Machine-readable legal-evidence readiness boundaries and explicit experimental opt-in for the
  lower-performing remediation Agent path.

- Resumable CSV/XLSX product review jobs with a downloadable template, row-level workflow IDs,
  JSONL checkpoints, bounded retries, and formula-safe CSV export.
- Section-level policy change impact analysis that finds affected workflow evidence and reviewed
  Agent memories, then creates idempotent re-review jobs without activating the staged source.
- Batched runtime telemetry with 1-hour, 24-hour, and 7-day P50/P95/P99, success-rate,
  request-rate, token, and query-rewrite cache summaries in the internal console.
- An isolated SQLite/PostgreSQL backend benchmark command guarded against non-benchmark databases.
- Alembic-managed runtime metric storage and PostgreSQL connection-pool defaults.
- Repository governance with CI, pull-request templates, ownership, and release conventions.
- Controlled reviewed-case Agent memory with scoped recall and source-version invalidation.
- Human maintenance APIs for reviewed Agent memory and auditable context provenance.
- Bidirectional Chinese/English legal query routing with original-plus-rewrite RRF retrieval.
- Cited minimal remediation diffs, protected-fact checks, and deterministic draft rechecks.
- Versioned, machine-enforced jurisdiction, tool, evidence, review, and side-effect guardrails.
- Reproducible Chinese-query/English-law evaluation across BM25, local Jina Dense, Hybrid RRF,
  and Sol query rewriting, with atomic rewrite caching and explicit BGE-M3 availability failures.
- Persistent human evaluation decisions and a consolidated legal/evaluation/memory review queue.
- Near-domain abstention calibration and narrow remediation-quality evaluation suites.
- Management workbenches for review decisions, evidence-linked diffs, draft rechecks, and memory provenance.
- Alembic migrations with SQLite round-trip and PostgreSQL 16 CI coverage.
- Credential-redacted weekly/manual smoke checks for model, OCR, and official-source dependencies.
- Versioned data evidence release with 11 source copies/3,439 sections, dataset hashes, sanitized
  benchmark results, runtime counts, and CI-enforced truthfulness checks.
- Scale evidence with 120 isolated RAG queries, 100 campaign workflows, 100 remediation inputs,
  20 generated complex PDFs/200 pages, concurrency metrics, and a 320-item human-review packet.

## [0.1.0] - 2026-07-21

### Added

- Evidence-first CN/US/EU compliance workflow and versioned policy retrieval.
- BM25, dense, hybrid RRF, conditional query rewriting, and evaluation harnesses.
- Complex PDF ingestion, staged human correction, OCR sidecars, and quality evaluation.
- Bounded remediation Agent, deterministic fallback pipeline, MCP tools, and operations console.

[Unreleased]: https://github.com/pzhwzsh/PolicyGuard-AI/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pzhwzsh/PolicyGuard-AI/releases/tag/v0.1.0

