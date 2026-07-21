# Project completion audit

Date: 2026-07-21

## Current maturity

PolicyGuard AI is an interview-grade local engineering project with a complete demonstrable path:
official-source ingestion, versioned RAG, cross-language retrieval, evidence review, bounded Agent
planning, internal remediation drafts, controlled memory, PDF processing, MCP, and operations UI.

It is not yet a production legal-compliance product. “Complete” is evaluated at three different
levels:

| Target | Current estimate | Meaning |
|---|---:|---|
| Interview-grade local project | 85% | Architecture and core paths are demonstrable and measured. |
| Production single-tenant service | 55% | Persistence, migrations, observability, auth, and live SLO evidence remain incomplete. |
| Commercial legal-compliance product | 25% | Jurisdiction coverage, legal validation, liability process, and continuous expert review are far larger than the software implementation. |

These percentages are engineering judgment, not measured product KPIs.

## Verified engineering state

- 88 automated tests pass.
- Core API/application/domain/infrastructure/MCP statement coverage is 84%.
- Whole-package coverage is 68% because many CLI, benchmark, backup, and worker entry points are not
  invoked by unit tests.
- Latest GitHub Actions passes in a clean Python 3.12 environment.
- Active retrieval remains 3 reviewed documents and 13 chunks; staged documents do not count as
  active law.
- The 22-query Chinese-to-English-law dataset passed an AI source audit, but has 0 human-verified
  samples.
- Local Jina is measured; BGE-M3 is not measured because the relay lacks `/embeddings` and the
  installed FastEmbed runtime does not support it.

## P0: required before calling the interview project complete

1. **Human ground truth:** have a person review the cross-language set, PDF labels, citation labels,
   and remediation meaning-preservation cases. Keep reviewer identity optional but record date and
   decision history.
2. **Active legal coverage:** legally review and activate the staged SAMR, FTC, and CELLAR documents.
   Report active document/chunk counts separately from downloaded or staged counts.
3. **Abstention calibration:** expand near-domain no-answer data and measure precision, recall,
   false-answer rate, and citation entailment. Dense candidate presence cannot be treated as an
   answer.
4. **Remediation quality evaluation:** measure claim-span accuracy, correct-article rate, protected
   fact retention, semantic-intent preservation, and post-rewrite residual risk. The current
   deterministic rewrite covers a narrow risky-phrase baseline.
5. **UI integration:** expose cross-language rewrite provenance, cited field-level findings,
   before/after diff, post-check result, and recalled-memory provenance in the management console.
6. **Schema migrations:** replace `create_all`-only evolution with Alembic migrations and test an
   upgrade from the current SQLite schema. Add a PostgreSQL integration test.
7. **Live smoke suite:** run bounded nightly/manual provider checks for LLM, Embedding, OCR, and
   official sources without putting paid or flaky calls in ordinary CI.

## P1: high-value product capabilities

- Reviewer work queue with assignment, comments, evidence acceptance, and remediation approval.
- Batch CSV/XLSX product review with per-row evidence and resumable jobs.
- Image and video advertising analysis: OCR, visual claims, disclaimers, subtitles, and claim-to-frame
  citations.
- Jurisdiction packs with effective dates, category rules, platform policies, and conflict matrices.
- Law-change impact analysis that identifies affected products, memories, reports, and cached
  decisions before re-review.
- Evidence graph connecting claim spans, retrieved chunks, official versions, reviewer decisions,
  rewrites, and final drafts.
- Configurable retention/deletion for uploaded source files, extracted content, and audit logs.
- Exportable review package containing original content, exact citations, model usage, decisions,
  diff, and immutable hashes.

## P2: useful after P0/P1 evidence exists

- Approved-claim library and reusable country/category templates.
- “What changes by market?” comparison for the same campaign.
- Policy-change timeline and trend dashboard.
- Webhooks and SDK for product-information or campaign-management systems.
- MCP tools for reviewed memory, impact analysis, and report retrieval; keep mutation tools gated.
- Cost/latency routing between local models, inexpensive models, and Sol medium based on measured
  ambiguity rather than fixed branding.

## Explicit non-goals for the current local version

- No autonomous legal conclusion.
- No automatic external publishing.
- No automatic activation of downloaded law.
- No unlimited chat history or self-written Agent memory.
- No public multi-tenant deployment or login system until deployment is in scope.

