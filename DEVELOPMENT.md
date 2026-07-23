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
