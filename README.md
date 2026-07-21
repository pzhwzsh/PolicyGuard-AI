# PolicyGuard AI

Repository standards: [contributing guide](CONTRIBUTING.md),
[Git workflow](docs/GIT_WORKFLOW.md), [enforced product boundaries](POLICY.md),
and [changelog](CHANGELOG.md).

Cross-language retrieval has a reproducible provisional evaluation covering Chinese questions
against English US/EU sources. See
[cross-language retrieval evaluation](docs/evaluation/cross-language-retrieval-v1.md) and the
[human review checklist](data/evaluation/rag-cross-lingual-review-checklist.md). Its metrics are not
human-verified and must not be represented as production quality. The AI source audit and its two
query corrections are recorded in
[cross-language-source-audit-v1.md](docs/evaluation/cross-language-source-audit-v1.md).

## Current End-to-End Capabilities (2026-07-21)

### July 21 completion pass

### July 21 full local completion pass

- EU retrieval no longer depends on EUR-Lex pages that returned HTTP 202. The official Publications Office CELLAR chain is implemented: CELEX Work RDF -> English Expression -> `application/xhtml+xml`. Six EU documents were fetched and staged with official titles.
- Source quality now separates catalogs from legal documents. FTC catalog pages are structurally blocked; activation candidates are cleaned to SAMR 74, FTC Health 205, and CAN-SPAM 59 sections, plus six CELLAR EU documents. Structural review and legal review are separate states; activation requires explicit legal-review confirmation.
- RAG evaluation now has an 80-question human-authored development suite and a separate 180-query robustness set. The robustness set contains 45 originals plus 135 deterministic variants and is never represented as 180 independent human labels. Jina Dense scored Hit@5 1.0 / MRR 0.9329; BM25 scored 0.4222 / 0.4167.
- Dynamic routing evaluation covers native PDF, OCR, complex tables, query rewriting, evidence failure, prompt injection, and ambiguous priority. The deterministic router scored 10/10 with full manual-review recall; this is the routing baseline before spending Agent tokens.
- Local operations now include optional `X-Admin-Key` protection for management writes, per-IP rate limiting, write audit logs, PDF active-content rejection, job listing/manual retry, evaluation dashboard, report history, PDF original-vs-block comparison, source diff links, and consistent SQLite/file backups with SHA256 manifests.
- Complete verification now collects 97 tests. Locally, 96 pass and the PostgreSQL-only migration test is conditionally skipped; GitHub CI provisions PostgreSQL 16 to run that final case. Core API/application/domain/infrastructure/MCP statement coverage was last measured at 84%; whole-package coverage was 68% because CLI and worker entry points are not broadly unit-tested. Latest management UI was checked at desktop and 390px mobile width with no horizontal overflow or console errors.

- Official registry: 11 SAMR/FTC/EU Publications Office sources across CN/US/EU. Real SAMR/FTC snapshots and six CELLAR EU documents are staged for review; active retrieval remains 3 reviewed documents/13 chunks until a reviewer approves new versions.
- Source update workflow: canonical visible-text hashing, raw snapshots, conditional requests, bounded 202 polling, unified diffs, staged HTML parsing, manual version approval, old-version effective dates, historical `as_of` retrieval, and queued re-embedding.
- Document workflow: synchronous and persistent asynchronous PDF ingestion, native parsing, a running RapidOCR/ONNX CPU sidecar, optional PaddleOCR/PP-Structure/MinerU adapters, block-level correction with optimistic revision checks, correction history, staged approval, and active indexing.
- Report exports: deterministic JSON, Markdown, and PDF generated from persisted claims, evidence, exact source links, review decisions, and model usage.
- Background jobs: SQLite-persisted `parse_document`, `source_monitor`, and `reindex_embeddings` jobs with idempotency keys, retries, exponential backoff, stale-worker recovery, and status APIs.
- Verification: 66 automated tests pass. The RapidOCR smoke PDF produced one canonical block with the exact text `Advertising must be truthful`, page/bbox/hash metadata, and confidence 0.9963.
- Real PDF evaluation has started with the official 42-page FTC Health Products Compliance Guidance. Only visually reviewed pages 2-3 are currently labeled: pdfplumber text accuracy 0.9714, heading F1 1.0, reading-order accuracy 1.0, and 143.69 ms/page on this machine. One document/two pages is not a reportable production benchmark.

Current 80-question development RAG suite:

| Retriever | Baseline Hit@5 / MRR | Hard Hit@5 / MRR | Elapsed |
|---|---:|---:|---:|
| BM25 | 1.000 / 1.000 | 0.133 / 0.133 | 0.226 s |
| Jina Dense | 1.000 / 0.933 | 1.000 / 0.911 | 10.189 s |
| Hybrid RRF | 1.000 / 0.933 | 1.000 / 0.911 | 10.666 s |

The suite contains 45 answerable and 35 no-answer questions. It is human-authored development data, not production traffic. Near-domain negative queries still produced candidates 100% of the time under dense retrieval, so candidate retrieval must not be treated as answerability.

Agent vs deterministic pipeline on eight remediation cases:

| Mode | Success | Manual review | Mean latency | Tokens |
|---|---:|---:|---:|---:|
| Deterministic pipeline | 100% | 0% | ~0.01 ms | 0 |
| Sol medium Agent | 37.5% | 12.5% | 13.67 s | 73,100 |

This result is why `mode=pipeline` remains the default. `mode=agent` is available for ambiguous tool-selection experiments, with allowlists, step/tool budgets, trace persistence, and manual fallback; it is not presented as an automatic improvement.

- Official-source monitoring with conditional requests, raw snapshots, content hashes, and append-only history. Changed HTML is staged for review instead of silently replacing legal text.
- Jina local ONNX dense retrieval plus BM25/RRF, selected using easy, cross-language hard, and no-answer datasets.
- Sol medium claim extraction and evidence-support verification. A supported decision requires an exact quote present in retrieved evidence.
- Bounded tool-using agent core with tool allowlists, maximum steps/tool calls, prompt-injection blocking, redaction, and manual-review fallback.
- Controlled Agent memory: prompt assembly has explicit evidence/memory budgets; at most three human-confirmed, scope-compatible remediation cases are recalled with `run_id` provenance. Activating a new version of an official source invalidates memories based on the old version. There is no unbounded chat history or autonomous memory write.
- No-login web console for product review, market evidence, workflow events, human review, and PDF upload.
- Review workbench for accepting, correcting, or rejecting evaluation labels, viewing pending legal versions, and inspecting invalidated Agent memories. Downloaded law is never automatically approved.
- Development abstention calibration over 20 near-domain no-answer queries and a six-case remediation-quality suite. These are development metrics pending human labels, not production claims.
- Alembic schema lifecycle with SQLite round-trip coverage and a PostgreSQL 16 CI integration test.
- Weekly/manual external smoke checks for LLM, Embedding, OCR, and all 11 registered official sources. Reports expose host, status code, and latency but never credentials; access-controlled sources are distinguished from actual failures.
- PDF ingestion at `POST /api/v1/documents/parse`: canonical layout JSON is the source of truth; Markdown and RAG chunks are derived outputs. Native text, tables, headings, images, page numbers, section paths, and table header paths are preserved when available. Complex/scanned pages return `review_required` until MinerU/OCR adapters are installed.
- Conditional query rewriting is active in the compliance workflow when an LLM is configured. The original query is always retained, up to three rewrites are fused with RRF, and rewrites that add actors, penalties, approvals, dates, or numeric constraints are rejected. Workflow events expose trigger reasons, cache hits/misses, and provider token usage.
- PDF parser routing supports isolated MinerU, PaddleOCR, and PP-Structure sidecars. Unconfigured or failed sidecars degrade to native parsing plus manual review. A parsed upload remains `staged` until `POST /api/v1/documents/{document_id}/approve` supplies official source metadata and a reviewer; only then is it added to active retrieval.
- Ground-truth PDF evaluation reports text accuracy, heading F1, table-cell accuracy, reading-order accuracy, per-page latency, and manual-correction rate. Runtime parser confidence is deliberately not reported as extraction accuracy.

## Query rewriting decision

On the 30-question development hard set, Jina retrieval scored `HitRate@5=1.000` and `MRR=0.9111`. Canonical rewriting reached `MRR=0.9400`; original-plus-rewrites RRF reached `MRR=0.9444`, a 3.7% relative MRR gain with no HitRate gain. The first run used 3 Sol medium requests, 17,439 tokens, and 68.1 seconds; the cached rerun used no model calls or tokens and took about 24 ms. Therefore rewriting is conditional and cached, never a replacement for the original query. These are development-set measurements, not production claims.

Configure the optional PDF sidecars only when their services are actually running:

```env
MINERU_BASE_URL=http://127.0.0.1:9010
PADDLEOCR_BASE_URL=http://127.0.0.1:9011
PPSTRUCTURE_BASE_URL=http://127.0.0.1:9012
DOCUMENT_PARSER_API_KEY=
```

Each service implements `POST /parse`, accepts multipart PDF input, and returns the canonical `ParsedDocument` JSON contract. Keep heavyweight OCR runtimes outside the FastAPI process so parser crashes and GPU dependencies cannot take down the review API.

Start the API, job worker, and the verified lightweight OCR sidecar:

```powershell
docker compose -f deploy/parsers/compose.yml up -d rapidocr
.\.venv\Scripts\python.exe -m policyguard.scripts.job_worker
.\.venv\Scripts\python.exe -m uvicorn policyguard.api.main:app --port 8002
```

Create a consistent local backup:

```powershell
.\.venv\Scripts\python.exe -m policyguard.scripts.backup_local
```

For a protected local management console set `ADMIN_API_KEY`; ordinary review/search/report reads remain available, while source activation, corrections, monitoring triggers, asynchronous uploads, and job retries require `X-Admin-Key`. Leave it empty for the original no-login local demo.

PaddleOCR/PP-Structure Compose definitions are present, but their image build is not claimed complete: Docker Hub mirror returned 403 and the Microsoft base-image pull made no progress. The RapidOCR sidecar was built locally and passed an end-to-end PDF OCR request. The labeled PDF manifest is still empty, so the smoke confidence is not an accuracy metric.

Run the monitor once or as a worker:

```powershell
python -m policyguard.scripts.check_source_updates
python -m policyguard.scripts.source_monitor_worker --interval-seconds 21600
```

Run the local console:

```powershell
.\.venv\Scripts\python.exe -m uvicorn policyguard.api.main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000/>. Public deployment remains intentionally deferred.

## Model benchmark and token budget

The candidate matrix is in `config/model_matrix.json`. It includes BGE-M3, Chinese BGE, multilingual E5, and hosted embedding candidates, plus reranker and LLM profiles. Names are configuration candidates, not claims that one model is best.

Run a real embedding comparison only after configuring an OpenAI-compatible endpoint and key:

```powershell
python -m policyguard.scripts.benchmark_embeddings
```

The command evaluates every candidate on the same dataset and filters, isolates the persistent vector cache by provider/model, and writes JSON/CSV to `data/benchmarks/` (ignored by git). Without credentials it prints `SKIPPED` and produces no fabricated metrics. The harness records HitRate@K, MRR, mean/P95 latency, estimated input tokens, and failures. Token counts are heuristic estimates unless the provider returns usage.

`compress_context` in `policyguard.application.benchmark` deduplicates and caps evidence before an LLM call. Cost controls include embedding and extraction caches, batched vectors, a rerank candidate cap, cheap models for deterministic classification, and escalation to a stronger model only for ambiguous/high-risk cases.

PolicyGuard AI 是一个“证据优先”的跨境商品营销合规审查与修复平台。它将从确定性规则基线逐步演进为可评测的多法域 RAG 系统，再演进为具备工具调用、失败恢复和人工审核的 Agent 工作流。

项目不把 Demo 指标包装成生产数据，也不会在尚未接入模型时宣称拥有 AI 分析能力。

## 业务目标

输入商品标题、描述、类目、属性和图片，系统输出：

- 合规风险及严重等级；
- 支撑判断的规则与来源；
- 建议修复方案；
- 自动通过、自动修复或转人工审核的决策；
- 可回放的执行轨迹、延迟、Token 与成本数据。

## 当前版本：V1a 词法检索基线

已经包含：

- FastAPI HTTP 服务；
- SQLite 持久化，接口保持向 PostgreSQL 迁移的边界；
- 可版本化的演示规则；
- 商品检查、风险发现、修复建议和检查记录查询；
- 结构化领域模型与测试；
- 健康检查与统一错误边界。
- 国家市场监督管理总局公开法规节选，保留来源、版本和抓取日期；
- 美国 FTC 广告与营销官方指南；
- 欧盟 EUR-Lex《不公平商业行为指令》相关条款；
- CN、US、EU 法域及品类、渠道、生效日期过滤；
- 文档与 Chunk 内容哈希、幂等入库；
- 面向中文的可解释 BM25 检索基线；
- 搜索结果中的原文条款和官方来源链接；
- 10 条人工标注的开发期冒烟评测集。
- OpenAI-compatible Embedding 适配器与SQLite向量缓存；
- BM25/Dense检索模式状态接口；
- Hybrid候选集后的可替换Reranker接口。
- 跨市场合规工作流：输入校验、声明基线提取、逐市场取证、人工复核路由和Checkpoint。
- 可选OpenAI-compatible LLM声明提取节点，失败时记录降级并回退基线。
- 受控修复工具、修复计划、二次确认和内部草稿生成。
- 基于官方Python MCP SDK的stdio Server。

当前不包含：已配置的真实Embedding模型、Rerank、LLM和OCR。Dense、Rerank、LLM和MCP适配器已经实现，但没有对应配置时不会调用外部服务；当前工作流仍不是自主Agent。

## 快速开始

```powershell
cd D:\DeskTop\PolicyGuard-AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m uvicorn policyguard.api.main:app --reload
```

打开：

- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

知识检索：

```text
GET /api/v1/knowledge/stats
GET /api/v1/knowledge/search?q=广告可以使用国家级吗&market=CN&top_k=5
GET /api/v1/knowledge/search?q=广告可以使用国家级吗&market=CN&mode=dense&top_k=5
GET /api/v1/knowledge/search?q=广告可以使用国家级吗&market=CN&mode=hybrid&top_k=5
GET /api/v1/knowledge/search?q=广告可以使用国家级吗&market=CN&mode=hybrid_rerank&top_k=5
GET /api/v1/knowledge/retrievers
POST /api/v1/knowledge/compare
POST /api/v1/workflows/compliance
GET  /api/v1/workflows/compliance/{run_id}
POST /api/v1/workflows/compliance/{run_id}/review
POST /api/v1/workflows/compliance/{run_id}/remediation-plan
POST /api/v1/workflows/compliance/{run_id}/draft
```

## MCP Server

安装可选依赖并启动stdio服务：

```powershell
python -m pip install -e ".[dev,mcp]"
python -m policyguard.mcp_server
```

当前MCP工具：

- `search_policy`：按市场、品类和渠道读取官方证据；
- `get_workflow`：读取工作流和事件Checkpoint；
- `create_remediation_plan`：审核接受后创建内部修复计划；
- `create_internal_draft`：二次确认后创建内部草稿。

MCP没有暴露外部发布或数据库任意写入工具。

Dense、Hybrid或Hybrid+Rerank模式需要先配置 `.env` 中的 OpenAI-compatible Embedding 服务；Hybrid+Rerank还需要配置 `/rerank`服务。未配置时接口分别返回 `503 embedding_not_configured` 或 `503 reranker_not_configured`，不会静默回退。Hybrid使用RRF融合排名，不直接相加BM25和余弦分数。

手动导入与评测：

```powershell
python -m policyguard.scripts.ingest_sources
python -m policyguard.scripts.evaluate_bm25
```

示例请求：

```powershell
$body = @{
  external_id = "SKU-001"
  title = "国家级护肤品，100%安全"
  description = "普通演示商品"
  category = "beauty"
  attributes = @{ brand = "Demo" }
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/checks `
  -ContentType application/json `
  -Body $body
```

> 内置关键词规则仍仅用于验证架构，不代表任何平台政策。知识库中的法规条款来自国家市场监督管理总局官方页面，但目前只是人工核对的相关条款节选，不能替代完整法律审查。

## 当前基线指标

开发期冒烟集共 15 条，覆盖 CN、US、EU 同语言检索，BM25 当前结果为 `HitRate@5 = 1.0`、`MRR = 1.0`。这个数据集规模小、问题简单，只能证明法域过滤和检索链路正确，不能作为简历中的效果指标。中文声明检索英文法规目前效果有限，是下一阶段多语 Dense Retrieval 必须解决和量化的问题。

## 多法域边界

检索顺序固定为：`法域 -> 品类 -> 渠道 -> 有效日期 -> 词法/语义检索`。适用法域不能由 LLM 单独猜测。法律、监管指南和平台政策分别记录权威层级；翻译文本可以帮助召回，但最终证据必须保留官方原文。

系统只提供公开规则风险筛查和候选证据，不构成法律意见，也不保证商品通过平台审核。

## 工作流边界

当前工作流使用确定性“标题+描述”作为声明提取基线，并将每个市场的候选条款写入事件轨迹。它不会自动下法律结论，也不会修改商品或调用外部发布接口。只有在后续接入模型、工具权限和人工审核后，才会演进为Agent工作流。

配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 后，工作流会尝试使用结构化声明提取；模型超时、返回非法JSON或接口错误时，会记录 `claim_extraction_fallback` 事件并回退到确定性基线。

人工审核请求必须包含唯一 `decision_id`。相同ID重复提交是幂等操作；工作流进入 `review_accepted` 或 `review_rejected` 后，新的冲突决策返回HTTP 409，防止重复审批覆盖终态。

审核接受后，修复工具只能创建计划；再次提供唯一 `execution_id` 后才生成内部草稿。原始输入不修改，两个阶段都标记 `external_side_effect=false`，当前没有任何外部发布工具。

## 演进路线

| 版本 | 目标 | 必须提供的证据 |
|---|---|---|
| V0 | 确定性规则基线（完成） | 功能测试、延迟、规则命中记录 |
| V1 | 可评测 RAG（进行中） | 固定测试集、Recall@K、MRR、引用正确率、BM25/Dense对照 |
| V2 | Agent 工作流 | 工具轨迹、成功率、重试/降级/人工恢复案例 |
| V3 | 工程化 | P50/P95、Token、成本、任务恢复、规则热更新 |
| V4 | 安全与故障演练 | Prompt Injection、越权、重复执行、服务重启测试 |

## 为什么从基线开始

如果没有不用 AI 的基线，就无法证明 RAG 或 Agent 带来了价值。确定性规则适合高确定性、高风险的检查；RAG 负责检索不断变化的规则证据；Agent 只负责输入不确定、需要动态选择工具的环节。

详细选型和替代方案见 [架构决策](docs/adr/0001-architecture.md)，开发约定见 [DEVELOPMENT.md](DEVELOPMENT.md)，项目交接状态见 [HANDOFF.md](HANDOFF.md)。
