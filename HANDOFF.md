# Handoff

Last updated: 2026-07-23

## Current state

- `main` contains the working API, management UI, RAG workflow, document ingestion, review flow,
  remediation flow, Agent memory, MCP server, migrations, and CI.
- Policy changes can be compared at section level and mapped to affected workflow evidence and
  reviewed Agent memories; re-review jobs are idempotent and do not auto-activate legal content.
- CSV/XLSX product files can be reviewed through resumable background jobs with bounded retries,
  row-level workflow IDs, and formula-safe CSV exports.
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

## Open work

### Data and evaluation

- Complete human review of retrieval, citation, PDF, and remediation evaluation samples.
- Add a representative set of real complex-layout PDFs.
- Re-run retrieval, abstention, citation, and remediation evaluation on a held-out reviewed set.
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

- Add image and video claim extraction with frame-level evidence.
- Add retention and deletion controls for uploaded files and derived artifacts.
- Add exportable review packages.

### Runtime

- Validate worker recovery and concurrency against PostgreSQL.
- Replace SQLite for concurrent job execution where write contention is material.
- Add operational monitoring for queues, parser failures, model calls, and source updates.

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
# After creating a private reviewed holdout file:
# python -m policyguard.scripts.audit_holdout data/evaluation/holdout-v1.json `
#   --development data/evaluation/rag-baseline.json data/evaluation/rag-hard-v1.json
python -m uvicorn policyguard.api.main:app --port 8002
```

Optional services:

```powershell
docker compose -f deploy/parsers/compose.yml up -d rapidocr
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
