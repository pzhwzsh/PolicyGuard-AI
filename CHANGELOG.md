# Changelog

All notable changes to PolicyGuard AI are documented here. The format follows Keep a Changelog,
and versions follow Semantic Versioning.

## [Unreleased]

### Changed

- Rebuilt the handoff and development guides as UTF-8, removed stale and contradictory project
  states, and defined one source of truth for change history, current status, remaining work, data
  evidence, and architecture decisions.
- Removed repeated deployment-status wording from the internal handoff.
- Removed audience-specific positioning language from engineering documentation.

### Added

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

