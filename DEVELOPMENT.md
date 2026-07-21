# Development Guide

## 开发原则

1. 先定义业务问题和验收指标，再选择框架。
2. 每项 AI 能力必须有非 AI 基线和固定测试集。
3. 所有规则、文档、模型、Prompt 和索引都需要版本号。
4. 外部副作用必须支持幂等、超时、重试和人工确认。
5. 指标只能来自可复现测试或真实运行记录。
6. 多法域检索必须先应用法域、品类、渠道和有效期过滤。
7. 机器译文只能辅助召回，证据必须保留官方原文和来源链接。

## 本地命令

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m uvicorn policyguard.api.main:app --reload
```

## 代码分层

```text
src/policyguard/
├── api/             HTTP 协议、参数校验、响应映射
├── application/     用例编排，不依赖 FastAPI
├── domain/          领域实体、枚举和规则语义
└── infrastructure/  数据库、模型供应商、向量库、队列适配器
```

依赖方向必须由外向内：`api -> application -> domain`。基础设施通过明确接口接入，领域层不得导入 FastAPI、SQLAlchemy、LangChain 或 LangGraph。

## 新增能力的验收门槛

### RAG

- 至少 100 条固定问题及人工证据标注；
- 保存 Chunk、Embedding、索引和 Rerank 版本；
- 对比 BM25、Dense、Hybrid、Hybrid+Rerank；
- 分别报告检索指标和生成指标；
- 没有可靠证据时必须拒答。
- Dense Provider必须可替换，未配置时不得静默回退到BM25；
- Embedding缓存必须绑定Provider、Model和Chunk版本。
- Rerank只能处理有限候选集，并必须记录候选数量、精排延迟和最终Top-K。

### Agent

- 先证明固定工作流不能合理覆盖该决策；
- Tool 必须有 JSON Schema、超时、错误码和幂等语义；
- 图必须有终止条件、最大步数和 Token 预算；
- Checkpoint 必须支持进程重启后恢复；
- 高风险写操作必须进入人工审核。
- 工作流必须先有明确的状态、事件和恢复契约，再引入LangGraph；
- 没有模型自主决策时，使用“workflow/baseline”命名，不使用“Agent”命名。
- LLM结构化输出必须做Schema校验；解析失败必须记录降级事件。
- 人工审核和外部写操作必须使用业务幂等键；终态冲突不得静默覆盖。
- 修复建议、内部草稿和外部发布必须是三个独立权限阶段。
- MCP工具必须声明最小权限；禁止提供数据库任意SQL和外部发布工具。

### 变更记录

每个重要实验在 `docs/experiments/` 记录：

```text
假设
基线
变量
数据集
结果
失败案例
结论
遗留问题
```

## 分支与提交

- `feat:` 新能力
- `fix:` 缺陷修复
- `test:` 测试和数据集
- `docs:` 文档或架构决策
- `refactor:` 不改变行为的重构

不要在同一次提交中混合模型实验、数据库迁移和界面改造。
# Current Development Checks

Run the complete local stack:

```powershell
docker compose -f deploy/parsers/compose.yml up -d rapidocr
.\.venv\Scripts\python.exe -m policyguard.scripts.job_worker
.\.venv\Scripts\python.exe -m uvicorn policyguard.api.main:app --port 8002
```

Useful evaluations:

```powershell
.\.venv\Scripts\python.exe -m policyguard.scripts.evaluate_rag_models
.\.venv\Scripts\python.exe -m policyguard.scripts.benchmark_agent
.\.venv\Scripts\python.exe -m policyguard.scripts.benchmark_agent --run-agent
.\.venv\Scripts\python.exe -m policyguard.scripts.evaluate_documents
.\.venv\Scripts\python.exe -m policyguard.scripts.build_rag_robustness
.\.venv\Scripts\python.exe -m policyguard.scripts.evaluate_rag_robustness
.\.venv\Scripts\python.exe -m policyguard.scripts.benchmark_agent_routing
.\.venv\Scripts\python.exe -m policyguard.scripts.fetch_cellar_sources
.\.venv\Scripts\python.exe -m policyguard.scripts.backup_local
```

`--run-agent` spends real provider tokens. `evaluate_documents` intentionally skips until `data/evaluation/pdf/manifest.json` contains manually verified samples.

Use `APP_ENV=test` so tests never call configured external LLM services:

```powershell
$env:APP_ENV='test'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
```

Query Rewrite tests must cover provider success, cache reuse, drift rejection, and provider failure fallback. PDF changes must cover staged output, parser routing, approval rejection, real knowledge activation, and the ground-truth quality metrics. Never replace these tests with live API calls.

The optional MinerU/PaddleOCR/PP-Structure processes use the canonical `POST /parse` sidecar contract documented in README. Do not install their conflicting GPU/runtime dependencies into the API environment.
# Database migrations

New databases must be created with Alembic:

```powershell
alembic upgrade head
```

Before adopting migrations for an existing local SQLite database, create a backup, verify its schema
matches the initial revision, then stamp it once:

```powershell
python -m policyguard.scripts.backup_local
alembic stamp 7b835d0aef2b
```

Future model changes require a new reviewed migration. Do not edit the initial revision or rely on
`Base.metadata.create_all` as a production migration mechanism. CI runs the migration against both
SQLite and PostgreSQL 16.
