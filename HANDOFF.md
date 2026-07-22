# Handoff

Last updated: 2026-07-22

## Current state

- `main` contains the working API, management UI, RAG workflow, document ingestion, review flow,
  remediation flow, Agent memory, MCP server, migrations, and CI.
- SQLite is the default local database. PostgreSQL migration coverage runs in CI.
- RapidOCR is the supported local OCR sidecar. Other parser sidecars are optional.
- Downloaded policy versions remain staged until explicitly reviewed and activated.
- Detailed dataset and evaluation status is maintained in `docs/DATA_CARD.md`.

## Open work

### Data and evaluation

- Complete human review of retrieval, citation, PDF, and remediation evaluation samples.
- Add a representative set of real complex-layout PDFs.
- Re-run retrieval, abstention, citation, and remediation evaluation on a held-out reviewed set.

### Knowledge lifecycle

- Review staged policy versions before activation.
- Add change-impact analysis for affected reports, caches, and Agent memories.
- Expand jurisdiction, category, channel, and platform-specific policy coverage.

### Product workflow

- Add resumable CSV/XLSX batch review.
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
- Human review queues contain pending items; an empty decision must not be treated as approval.

## Local handoff

```powershell
cd D:\DeskTop\PolicyGuard-AI
python -m pip install -e ".[dev,pdf,mcp]"
Copy-Item .env.example .env
alembic upgrade head
$env:APP_ENV='test'
python -m pytest
python -m ruff check .
python -m policyguard.scripts.publish_data_evidence --validate
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
