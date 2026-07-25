# Development

## Requirements

- Python 3.12 or 3.13
- Git
- Docker Desktop for optional parser sidecars
- PostgreSQL only when running PostgreSQL integration tests locally

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,pdf,mcp]"
Copy-Item .env.example .env
alembic upgrade head
```

Secrets belong in `.env`. Do not commit `.env`, databases, uploaded files, provider responses, or
credentials.

## Run

API and management UI:

```powershell
python -m uvicorn policyguard.api.main:app --reload --port 8002
```

Background worker:

```powershell
python -m policyguard.scripts.job_worker
```

RapidOCR sidecar:

```powershell
docker compose -f deploy/parsers/compose.yml up -d rapidocr
```

MCP server:

```powershell
policyguard-mcp
```

## Test

Unit and integration tests must not call paid or unstable external services.

```powershell
$env:APP_ENV='test'
$env:PYTHON_DOTENV_DISABLED='1'
python -m pytest
python -m ruff check .
python -m compileall -q src
python -m policyguard.scripts.publish_data_evidence --validate
```

`PYTHON_DOTENV_DISABLED=1` prevents tests from loading real model credentials from the local
`.env`. Real provider checks belong in a separate controlled smoke test.

CI runs the PostgreSQL migration test with `POSTGRES_TEST_URL`. Without that variable, the single
PostgreSQL-specific test is skipped locally.

External service checks belong in the manual or scheduled smoke workflow, not the ordinary test
suite.

Production-readiness evidence follows `docs/PRODUCTION_VALIDATION.md`. Keep load and recovery
reports tied to an exact commit and declared environment. The local resilience drill verifies
control flow only; it does not establish production throughput, RPO, or RTO.

Staged uploads and all derived parsing artifacts can be removed through the authenticated
`DELETE /api/v1/documents/{document_id}` endpoint. Automated retention cleanup is dry-run-first:

```powershell
python -m policyguard.scripts.cleanup_uploads --days 30
python -m policyguard.scripts.cleanup_uploads --days 30 --apply
```

Activated knowledge documents are never eligible for this cleanup. Each deletion leaves a minimal
metadata-only event under `UPLOAD_DIR/.deletion-audit/`; source text and parsed content are not
retained in that event.

Local SQLite backups include the database, uploads, source-update state, and a SHA-256 manifest.
Creation verifies the completed backup before returning. `--keep` only rotates directories with the
backup timestamp naming convention.

```powershell
python -m policyguard.scripts.backup_local --keep 7
python -m policyguard.scripts.restore_local data/backups/<timestamp>
# Review the dry-run target list, then apply. Existing targets move to data/restore-safety first.
python -m policyguard.scripts.restore_local data/backups/<timestamp> --apply
```

Use database-native backup and restore tooling for PostgreSQL; the local scripts intentionally
refuse non-SQLite URLs.

## Provider retries and runtime fallback

Model HTTP calls retry only transient transport failures and HTTP 408/425/429/500/502/503/504.
`PROVIDER_MAX_ATTEMPTS` is capped at 5; delays use bounded exponential backoff with jitter and honor
numeric `Retry-After`. Authentication failures are neither retried nor sent to a backup model.

When configured, the runtime order is:

1. claim extraction, query rewrite, and evidence verification: `LLM_MODEL`, then
   `LLM_FALLBACK_MODEL`, then the existing deterministic/original-query/human-review fallback;
2. retrieval: primary embedding Hybrid, `EMBEDDING_FALLBACK_MODEL` Hybrid, then BM25;
3. reranking: `RERANK_MODEL`, then `RERANK_FALLBACK_MODEL`, then unreranked Hybrid candidates.

Fallbacks are recorded in workflow events or retriever status instead of silently changing the
result path. Configure local BGE small as the latency-first embedding backup; it is not used unless
the primary retrieval strategy raises an error.

## PDF chunking

PDF ingestion preserves parser-produced blocks before applying token windows. `rag_chunks` does not
merge content across headings, paragraphs, tables, pages, or section paths. Blocks longer than the
default 800-token budget are split with 100 approximate tokens of overlap. The dependency-free
estimator treats each CJK character as one token and keeps Latin identifiers/terms together; it is
deterministic governance metadata, not a claim that every embedding provider uses the same tokenizer.

Each derived chunk records its strategy, token and character offsets, page, block type, section
path, source hash, and parser. Embedding input continues to prepend the stored section heading.

## Spreadsheet cleaning

CSV/XLSX batch uploads use a two-stage workflow. `POST /api/v1/batches/clean-preview` detects the
header in the first 20 rows of each Sheet, proposes mappings for SKU, category, title, description,
and markets, and returns a row-level preview without enqueueing a review job. The reviewer can edit
the mapping and then call `POST /api/v1/batches/cleaning/{table_id}/confirm` with the preview
revision. Confirmation is one-time and revision checked.

Cleaning is deterministic and makes zero model calls. It preserves identifiers such as `001` as
text, does not evaluate workbook formulas, records Sheet and source-row provenance, normalizes
supported markets, detects duplicate IDs, and produces formula-safe `cleaned-input.csv` and
`cleaning-report.csv` files. Invalid rows block confirmation unless `allow_partial` is set; even in
partial mode, at least one valid row is required. Inputs are capped at 5 MB, 1,000 data rows, and
100 columns.

## Local embedding choice

The 30-query difficult retrieval set was rerun on 2026-07-23 with the cached local ONNX models:

| Model | Hit@5 | MRR | Mean latency |
| --- | ---: | ---: | ---: |
| `BAAI/bge-small-zh-v1.5` | 0.867 | 0.673 | 12.41 ms |
| `jinaai/jina-embeddings-v2-base-zh` | 1.000 | 0.911 | 41.51 ms |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 0.933 | 0.797 | 45.18 ms |

Jina remains the default because the legal/compliance retrieval accuracy gain outweighs BGE's
roughly 3.3x lower mean latency. All three run locally, so ordinary embedding requests do not incur
per-token provider fees. The spreadsheet path does not invoke any of them. Reconsider BGE only for
a latency-first deployment after measuring the target corpus and accepting the accuracy tradeoff.
The machine-readable output is `data/benchmarks/local-embedding-hard-v1.json`.

## Evidence hardening

An evaluation result is reportable as held-out evidence only after `audit_holdout` passes. Keep the
working reviewed dataset private when its source requires it; never copy development questions into
the holdout merely to reach the minimum count.

```powershell
python -m policyguard.scripts.audit_holdout data/evaluation/holdout-v1.json `
  --development data/evaluation/rag-baseline.json data/evaluation/rag-hard-v1.json
python -m policyguard.scripts.audit_pdf_corpus
python -m policyguard.scripts.audit_evidence_readiness
python -m policyguard.scripts.fetch_pdf_corpus
```

`fetch_pdf_corpus` accepts only HTTP 200 responses beginning with a PDF signature. Downloaded real
documents remain unlabeled until the expected blocks are independently checked. A PDF download is
never an extraction-accuracy label.

For database evidence, point `POSTGRES_BENCHMARK_URL` only at a disposable database whose name
contains `benchmark`. The benchmark runs 1/4/8-worker workflow tests plus idempotent enqueue,
concurrent claim, completion, and stale-worker recovery checks.

LLM benchmark pricing belongs in the candidate's `pricing_usd_per_million_tokens` object using the
actual relay input/output prices. Leave it null when unknown; the report then emits no cost.

## Project structure

```text
src/policyguard/
├── api/             FastAPI routes and request/response models
├── application/     use cases, workflows, retrieval, evaluation
├── domain/          entities, enums, and business rules
├── infrastructure/  database and external-service adapters
├── scripts/         workers, data jobs, evaluation, and operations
└── web/             management UI assets

tests/               automated tests
migrations/          Alembic revisions
config/              versioned runtime and model configuration
data/evaluation/     evaluation datasets
data/evidence/       published reproducibility artifacts
deploy/parsers/      optional parser sidecars
docs/                architecture, data, evaluation, and operations documentation
```

Keep framework-specific code out of `domain`. Application code depends on domain contracts;
infrastructure implements external adapters.

## Database changes

Create a new Alembic revision for every schema change:

```powershell
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

Do not edit an applied revision. Include upgrade coverage for SQLite and PostgreSQL where the change
is database-specific.

## Implementation rules

- Add tests for new behavior, failure paths, and permission boundaries.
- Keep external providers behind adapters and configure them through environment variables.
- Record provider, model, prompt, source, document, and index versions where they affect results.
- Do not silently fall back between retrieval or parser modes with different semantics.
- Keep external writes idempotent and bounded by timeout, retry, and authorization checks.
- Require explicit review before activating policy versions or publishing generated changes.
- Label generated, authored, AI-reviewed, and human-reviewed data accurately.
- Source structural corrections must include the current revision and are never activation by
  themselves; legal approval remains a separate authenticated operation.

## Pull requests

Use one branch for one coherent change:

```text
feat/<name>
fix/<name>
test/<name>
docs/<name>
refactor/<name>
```

Before opening a pull request:

1. Rebase or merge the latest `main` as appropriate.
2. Run the relevant tests, full test suite, Ruff, and evidence validation.
3. Add a migration when the schema changes.
4. Update `CHANGELOG.md` for user-visible or architectural changes.
5. Update `HANDOFF.md` only when current state, open work, known issues, or commands change.
6. Update `docs/DATA_CARD.md` when data, labels, metrics, or limitations change.
7. Push the branch, open a pull request, wait for CI, merge, and delete the branch.

Commit types: `feat`, `fix`, `test`, `docs`, `refactor`, `ci`, `chore`.
