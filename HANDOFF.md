# Handoff

Last updated: 2026-07-27

## Current state

- Model changes can be introduced through a revision-locked canary at 5/10/25/50/100 percent;
  success, error, P95 latency, and human-rejection regressions trigger rollback.
- `tests/load/locustfile.py` and the resilience drill are executable validation tooling, not proof
  of production scale. No load result is reportable until the environment and raw outputs exist.

- `main` contains the working API, management UI, RAG workflow, document ingestion, review flow,
  remediation flow, Agent memory, MCP server, migrations, and CI.
- Policy changes can be compared at section level and mapped to affected workflow evidence and
  reviewed Agent memories; re-review jobs are idempotent and do not auto-activate legal content.
- CSV/XLSX product files can be reviewed through resumable background jobs with bounded retries,
  row-level workflow IDs, and formula-safe CSV exports.
- CSV/XLSX uploads now pass through a Multi-Sheet cleaning preview with editable field mappings,
  source-row provenance, row-level exceptions, and revision-checked human confirmation. Cleaning
  makes zero model calls; reviewers may submit only valid rows and download the full exception report.
- SQLite is the default local database. PostgreSQL migration coverage runs in CI.
- RapidOCR is the supported local OCR sidecar. Other parser sidecars are optional.
- Downloaded policy versions remain staged until explicitly reviewed and activated.
- Detailed dataset and evaluation status is maintained in `docs/DATA_CARD.md`.
- Evaluation governance now blocks reportable holdout metrics when labels are pending, the split is
  not frozen, or a query overlaps development data.
- PDF provenance/coverage is audited separately from extraction accuracy; unlabeled downloads do
  not become accuracy evidence.
- Remediation continues to use the deterministic pipeline by default. Agent execution requires an
  explicit experimental opt-in and remains subject to the existing tool and step budgets.
- Model smoke reports now include input/output tokens and P50/P95/P99. Cost is emitted only when
  the exact relay price is configured.
- Legacy source manifests are now structurally blocked until they pass quality schema v2, including
  heading hierarchy and temporal metadata checks.
- Synthetic template-expanded RAG rows are stress-test data only and are not reportable legal
  holdout evidence.
- Workflow review and draft approval require an authenticated reviewer identity whenever
  `ADMIN_API_KEY` is configured.
- Blocked staged sources now expose a versioned structural-correction endpoint for reviewer-supplied
  section headings and verified publication/effective dates; corrections never auto-activate content.
- Parsed PDF blocks now use deterministic structure-aware token windows (800-token budget,
  100-token overlap) while retaining page, block type, section path, and source offsets.
- A fresh 30-query difficult-set run keeps local Jina as the default: Hit@5 1.000/MRR 0.911 at
  41.51 ms mean latency, versus BGE small 0.867/0.673 at 12.41 ms and MiniLM 0.933/0.797 at 45.18 ms.
- Transient model-provider failures now receive bounded retry with backoff. Runtime routes through
  configured backup LLM/embedding/rerank models, then deterministic, BM25, Hybrid, or human-review
  fallbacks as appropriate; authentication failures fail immediately.
- Agent Planner now uses the same bounded provider retry policy, validates the returned action schema,
  records provider attempts, and can fall back to the configured LLM model. Invalid JSON/actions are
  treated as provider failures; 401/403 are not masked by the backup chain. The remaining production
  gap is end-to-end idempotency/status reconciliation when an upstream request times out after it
  may already have executed.
- Local backup creation verifies its manifest, and restore is dry-run-first with a pre-restore
  safety copy. Optional retention only removes timestamp-named backup directories.
- The user UI now includes a governed creative studio. It generates grounded ad-copy candidates and
  deterministic platform-sized main/SKU assets from a real source image, then requires revisioned
  human approval before an audit ZIP can be downloaded.
- Optional product scene generation uses an identity-preserving image-edit sidecar and remains a
  pending human identity-review candidate. Missing provider configuration returns an explicit 503.
- The user workspace has been rebuilt around a guided six-step flow, reusable revisioned product
  records, local draft recovery, a unified tenant task list, and live component readiness checks.
- Intake now blocks executable/archive mismatches and active PDF content, detects PII and prompt
  injection before model use, limits active tenant jobs, and exposes cancellation/dead-letter flows.
- Publish preflight returns red/yellow/green checks for claim grounding, prohibited terms, platform
  length and image dimensions, policy evidence, privacy, and AI scene identity review.
- The independent Agent Harness now records append-only run events and checkpoints, enforces
  revision and execution budgets, supports pause/resume/cancel and explicit permission approval,
  and exposes replayable SSE events without replacing the governed PolicyGuard workflow.
- Harness context is priority-aware and bounded; working, episodic, and reviewed long-term memory
  are separate. Skills are declarative, multi-agent execution is a bounded DAG, and Sandbox/MCP
  integrations report unconfigured state instead of faking availability.
- A versioned harness evaluation set reports task success, tool precision/recall, recovery signals,
  latency, tokens, and estimated cost. The user workspace includes controls and live metrics.
- Production identity now supports signed OIDC tokens discovered through issuer JWKS, tenant claims,
  and a user/reviewer/admin role hierarchy. Legacy keys remain available for controlled migration.
- `/metrics` exports protected low-cardinality Prometheus counters and duration sums; request logs
  are structured in production, W3C trace headers are returned, and OTLP export is opt-in.
- Production Compose has optional Prometheus/Grafana and TLS Nginx profiles. Security CI audits
  dependencies, repository secrets, and high/critical filesystem vulnerabilities.

## Open work

- Run the declared Locust matrix against an isolated PostgreSQL staging deployment and retain raw
  CSV plus infrastructure metrics.
- Execute worker termination, provider interruption, and backup restore against staging; record
  measured RPO/RTO rather than quoting the deterministic local drill as production evidence.
- Complete consented user trials with real task outcomes using the privacy-safe template.

### Data and evaluation

- Complete human review of retrieval, citation, PDF, and remediation evaluation samples.
- Add a representative set of real complex-layout PDFs.
- Re-run retrieval, abstention, citation, and remediation evaluation on a held-out reviewed set.
- A provisional 2026-07-27 BGE-M3 comparison is recorded in
  `docs/evaluation/cross-language-retrieval-v1.md` and
  `data/benchmarks/cross-language-jina-vs-bge-m3-v1.json`. On the current 22-query development
  set, BGE-M3 + cached query rewrite reached Hit@5 1.0/MRR 1.0, but the set is pending final human
  verification; repeat on a frozen holdout and target hardware before changing the production default.
- Have an independent reviewer complete and freeze at least 50 holdout labels; the repository only
  contains the validation contract and template.

### Knowledge lifecycle

- Review staged policy versions before activation.
- Re-parse and re-review all staged sources under quality schema v2; restore legal hierarchy for
  the six EU sources with generic headings and verify publication/effective dates.
- Use `PATCH /api/v1/source-updates/{source_id}/{content_hash}/structure` with the returned
  `revision`, then rerun impact analysis and submit the separate legal approval.
- Expand jurisdiction, category, channel, and platform-specific policy coverage.

### Product workflow

- Connect reviewed image/video OCR frames to the same claim-to-policy evidence workflow; the media
  sidecar and frame provenance are present, but multimodal semantic claim matching still needs a
  reviewed evaluation set.
- Evaluate scene-image identity preservation on a reviewed product/SKU set before treating any
  external image model as production-ready.

### Runtime

- Validate worker recovery and concurrency against PostgreSQL.
- Replace SQLite for concurrent job execution where write contention is material.
- Connect the dashboard alert payload to the deployment's external pager/notification channel.
- Validate the opt-in Docker sandbox against the production container runtime before enabling it.
- Configure and integration-test approved HTTPS MCP servers before exposing their tools to runs.

## Known issues

- Some official-source endpoints may return access controls or asynchronous responses.
- Heavyweight PDF parser sidecars are not part of the default local environment.
- External LLM, embedding, rerank, and OCR availability depends on local `.env` configuration.
- EUR-Lex direct PDF endpoints returned an AWS WAF HTTP 202 challenge on 2026-07-23; use a permitted
  official download path or manually source the declared documents before annotation.
- Human review queues contain pending items; an empty decision must not be treated as approval.

## Local handoff

```powershell
cd D:\DeskTop\PolicyGuard-AI
python -m pip install -e ".[dev,pdf,mcp]"
Copy-Item .env.example .env
alembic upgrade head
$env:APP_ENV='test'
$env:PYTHON_DOTENV_DISABLED='1'
python -m pytest
python -m ruff check .
python -m policyguard.scripts.publish_data_evidence --validate
python -m policyguard.scripts.audit_pdf_corpus
# Preview staged uploads older than 30 days; add --apply only after reviewing the list.
python -m policyguard.scripts.cleanup_uploads --days 30
# Batch retention is inventory-only; actual deletion requires the authenticated two-stage API.
python -m policyguard.scripts.cleanup_batches --days 30
# After creating a private reviewed holdout file:
# python -m policyguard.scripts.audit_holdout data/evaluation/holdout-v1.json `
#   --development data/evaluation/rag-baseline.json data/evaluation/rag-hard-v1.json
python -m uvicorn policyguard.api.main:app --port 8002
```

Optional services:

```powershell
docker compose -f deploy/parsers/compose.yml up -d rapidocr
docker compose -f docker-compose.prod.yml up -d --build
python -m policyguard.scripts.job_worker
policyguard-mcp
```

## References

- Product overview: `README.md`
- Environment variables: `.env.example`
- Development setup and conventions: `DEVELOPMENT.md`
- Change history: `CHANGELOG.md`
- Data and evaluation status: `docs/DATA_CARD.md`
- Git workflow: `docs/GIT_WORKFLOW.md`
- Architecture decisions: `docs/adr/`
