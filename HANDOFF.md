# Project Handoff

## 2026-07-21 Handoff

Latest completion pass: 75 tests pass. The current local API is `http://127.0.0.1:8002/`; the RapidOCR canonical sidecar is running at `http://127.0.0.1:9013/`.

Full local completion update: 75 tests pass. Six EU sources now use the official CELLAR RDF/expression/XHTML path instead of the failing EUR-Lex HTML route. Eleven latest source updates are visible in the console; two FTC catalogs are activation-blocked and nine legal/guidance sources remain pending explicit legal review. No source was silently auto-activated.

The operations console now shows evaluation artifacts, persistent job status/retry, and report history. PDF correction includes the original PDF beside editable blocks. Management writes support optional `X-Admin-Key`, per-IP rate limiting, PDF active-content rejection, and audit rows. `backup_local` created a real SQLite/files backup with checksums.

The separate robustness set has 180 queries (45 human originals plus 135 labeled synthetic variants): Dense Hit@5/MRR 1.0/0.9329 versus BM25 0.4222/0.4167. Do not describe these as 180 independent human annotations.

Eleven official sources are registered. Five real SAMR/FTC responses were snapshotted and parsed into 556 staged sections, but they remain inactive pending structural/legal review. Active RAG truth is still 3 documents/13 chunks. Six EUR-Lex sources remain failed because the service continues returning HTTP 202 after bounded polling.

The unified RAG suite now has 80 unique development questions. BM25 hard-set Hit@5/MRR is 0.1333/0.1333; Jina Dense and Hybrid are 1.0/0.9111. The negative slices show that dense retrieval nearly always finds a candidate, so evidence verification remains mandatory.

Sol medium Agent evaluation used 73,100 tokens across eight cases and achieved only 37.5% success versus the deterministic pipeline's 100%. Keep Pipeline as the default remediation mode. The Agent path is useful for demonstrating bounded planning, traces, budgets, and failure routing, not for replacing deterministic rules.

PDF work now includes a built and running RapidOCR/ONNX CPU sidecar, staged async jobs, human block correction, revision history, report exports, and approval. PaddleOCR/PP-Structure definitions exist but their image build was blocked by the configured Docker mirror/MCR download. One real FTC PDF has two visually reviewed labeled pages; this validates the metric pipeline but is not enough to claim general extraction accuracy.

The requested engineering areas now have working development implementations: formal RAG/evidence workflow integration, official-source monitoring and evaluation datasets, bounded Agent execution, security/trace controls, conditional query rewriting, staged PDF activation, and a no-login management UI. The isolated project environment passes 49 tests.

PDF ingestion now uses canonical JSON plus derived Markdown. The tested native-PDF fast path preserves pages, headings, table cells, multi-level header paths, image placeholders, and staged RAG chunks. MinerU, PaddleOCR, and PP-Structure have HTTP sidecar adapters and deterministic routing, but those heavyweight services are not installed on this machine and must not be claimed as locally operational. Sidecar failures are recorded and fall back to manual review. Parsed documents require reviewer and official-source metadata before activation; review-required or empty documents cannot be activated.

Query rewriting is connected to the production workflow. It triggers for CN language mismatch or retrieval top score below 0.45, always retains the original query, and combines original plus rewrites with RRF. Cache hits/misses and provider token usage are workflow events. High-risk added constraints are rejected per market. On the 30-question development set, MRR moved from 0.9111 to 0.9444; the uncached experiment cost 17,439 Sol medium tokens and 68.1 seconds, while the cached rerun made zero LLM calls. This limited gain is why rewriting remains conditional.

`document_quality.py` implements ground-truth metrics for text, headings, table cells, reading order, per-page latency, and manual correction. No production PDF corpus has been labeled yet, so no PDF accuracy number should be claimed.

Source monitoring stores raw snapshots and detects changes. SAMR and FTC established real baselines; EUR-Lex returned a pending/empty response and was correctly recorded as failed. Automatic activation is allowed only for future structured, schema-validated official feeds. HTML changes require parsing, diff review, and version approval before they enter the active RAG index.

## Model benchmark status

The benchmark harness is implemented in `src/policyguard/application/benchmark.py` with the CLI `python -m policyguard.scripts.benchmark_embeddings`. `config/model_matrix.json` lists candidate embedding, rerank, and LLM profiles. The embedding runner uses one fixed evaluation set, the same market filters, and provider/model-scoped cache keys so comparisons are fair. It reports HitRate@K, MRR, mean/P95 latency, estimated tokens, and failures, and writes ignored artifacts under `data/benchmarks/`.

No real BGE-M3, E5, or hosted-model score is claimed yet: a provider endpoint and credentials must be configured before the CLI runs. Offline tests use a deterministic fake provider only to validate the harness. `estimate_tokens` is explicitly heuristic; provider-reported usage should be recorded when available. Context compression, cache reuse, batching, candidate caps, and escalation to stronger models are the current token/cost controls.

最后更新：2026-07-19

## 产品定位

面向 AI 应用工程岗位的深度主项目：以商品合规为真实业务场景，展示 RAG、Agent、MCP、后端工程、评测和故障恢复能力。

## 当前状态

阶段：V1a 多法域词法检索基线。

已完成：

- 项目架构、开发约定和技术选型文档；
- FastAPI 应用入口；
- SQLAlchemy 持久化；
- 商品检查领域模型；
- 演示规则初始化；
- 创建检查与查询检查接口；
- 单元/API 测试骨架。
- 国家市场监督管理总局广告法相关条款数据；
- 来源、版本、抓取时间和内容哈希；
- 文档与 Chunk 持久化；
- 中文字符单字/双字 BM25；
- 知识检索、统计接口和离线评测命令。
- CN、US、EU 法域模型；
- 品类、渠道、法律层级、原文语言、翻译状态和有效期元数据；
- 带适用范围过滤的检索；
- 跨市场候选证据比较接口。
- OpenAI-compatible Embedding 适配器；
- Chunk向量持久化缓存；
- Dense模式未配置时的明确错误边界。
- RRF混合检索模式，避免直接比较不同检索器的原始分数。
- Hybrid候选集后的Reranker适配器和错误边界。
- 跨市场合规工作流和SQLite事件Checkpoint。
- 可选LLM声明提取器、JSON校验和显式降级事件。
- 人工审核接受/拒绝、幂等decision_id和终态冲突检测。
- 修复工具注册表、修复计划和内部草稿二次确认。
- 官方Python MCP SDK stdio Server和4个受控工具。

未完成且不得宣称已完成：

- 电商平台专属规则数据；
- PDF/OCR/表格解析；
- Dense Retrieval 与 Rerank；
- 达到可报告规模的 RAG 测试集；
- LangGraph、SSE、任务队列；
- LLM 调用和 Agent 评测；
- 公网部署。

## 当前验证

- 自动化测试：16 passed；
- 官方来源文档：3份；
- 当前Chunk：13个；
- 冒烟集：15条；
- BM25 HitRate@5：1.0000；
- BM25 MRR：1.0000。

以上检索指标仅证明链路正确，样本量太小，不得写入简历作为项目效果。

## 下一阶段：V1b 混合检索

建议按以下顺序推进：

1. 扩充到至少 100 条问题，加入困难负例、相似条款和无答案问题；
2. 建立 HTML/PDF 解析质量测试；
3. 加入中文 Embedding 与 pgvector；
4. 对比 BM25 与 Dense Retrieval；
5. 用 RRF 融合，再加入 Rerank；
6. 输出 BM25、Dense、Hybrid(RRF)、Hybrid+Rerank 的 Recall@K、MRR、延迟和失败案例。

当前最重要的实验问题：中文商品声明对英文 FTC/EUR-Lex 原文的 BM25 召回较弱。下一阶段应使用多语 Embedding 测试跨语言召回，再用 RRF 与词法结果融合，不能通过人工翻译查询掩盖这个问题。

## Embedding配置

只接受 OpenAI-compatible `/embeddings`接口：

```env
EMBEDDING_BASE_URL=https://provider.example/v1
EMBEDDING_API_KEY=本地配置，不提交
EMBEDDING_MODEL=provider-supported-embedding-model
```

配置后先运行 `GET /api/v1/knowledge/retrievers` 确认 `dense_available=true`，再用 `mode=dense` 查询。当前没有真实Dense指标，不能在简历中声称支持多语语义检索。

Rerank同样尚未配置真实服务；当前只有Fake Provider测试精排顺序，不能写成真实精排效果。

MCP启动：`python -m pip install -e ".[dev,mcp]"` 后运行 `python -m policyguard.mcp_server`。工具只允许证据读取、工作流读取和内部计划/草稿操作。

LLM声明提取同样尚未配置真实服务。配置中转API后，工作流会尝试结构化输出；失败会回退并留下事件，不会静默吞掉错误。

## 工作流当前状态

`POST /api/v1/workflows/compliance` 已实现确定性基线：输入校验 -> 标题/描述声明基线 -> 按市场收集证据 -> `review_required` 或 `needs_more_evidence`。每个步骤记录到 `workflow_events`，可通过run_id查询恢复。

`POST /api/v1/workflows/compliance/{run_id}/review` 支持 `accept/reject`。相同decision_id重复提交不会产生新事件；终态后使用新decision_id覆盖会返回409。

审核接受后可调用 `/remediation-plan`，再调用 `/draft`。计划和执行分别使用 `plan_id`、`execution_id` 做幂等；只生成内部草稿，不修改原始输入或外部商品。

下一步接入LangGraph时，保留这些状态和事件契约，把声明提取、查询改写、工具选择和复核节点替换为可评测节点；不要直接把当前确定性流程改名为Agent。

## 关键风险

- 内置规则是演示数据，不得写进简历作为真实平台政策；
- 先做数据和评测，再接模型；
- 不要为了“多 Agent”拆出没有独立状态或权限边界的角色；
- 模型API密钥只能放在本地 `.env`；
- 自动修复在真实写入外部系统前必须有人工审批和幂等保护。

## 推荐交接检查

```powershell
cd D:\DeskTop\PolicyGuard-AI
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m uvicorn policyguard.api.main:app
```

## 面试证据日志

后续每个里程碑都要在本文件补充：样本数量、实验日期、代码版本、指标、失败案例以及仍未解决的问题。
# Latest completion audit

The current branch collects 99 tests: 98 pass locally and one PostgreSQL migration integration test
is skipped without `POSTGRES_TEST_URL`. GitHub CI provisions PostgreSQL 16 for that test. Whole-package
statement coverage is 69%. Cross-language retrieval, reviewed memory lifecycle,
evidence-grounded remediation, and machine guardrails are integrated. The Chinese-to-English-law
dataset has an AI source audit with two corrected queries but still has zero human-verified samples.
See `docs/roadmap/project-completion-audit.md` for the remaining P0/P1/P2 work.

# Agent context and memory handoff

- `AgentContextBuilder` assembles remediation context and records estimated budget/provenance metadata.
- SQLite `agent_memories` contains reviewed episodic cases; workflow checkpoints remain separate.
- A case is written only after `create_draft` receives a human `approved_by` value.
- Recall filters task type, jurisdiction, category, channel, confirmation, and invalidation state.
- Policy activation invalidates memories whose recorded source version is no longer active.
- This is deliberately not user-profile memory, chat history, or autonomous self-learning.

# Seven-item closure

- Human review records and a management queue now support accept/correct/reject decisions. The
  22-sample cross-language dataset still has zero real human confirmations.
- Staged official documents expose review state and diff, and activation requires an explicit
  reviewer plus legal confirmation. Automatic activation remains forbidden.
- Abstention calibration uses 20 near-domain negatives. The same-set development result is threshold
  0.397214, precision/recall/F1 0.944444, false-answer rate 0.05; it is not a deployment threshold.
- Remediation evaluation uses six narrow cases and checks spans, cited sections, protected facts and
  residual risk. All development checks pass, but semantic preservation still needs human labels.
- The console exposes the review queue, corrections, memory provenance, remediation diff, citations,
  and draft recheck.
- Alembic owns new schema creation; SQLite round-trip is verified locally and PostgreSQL is exercised
  in CI.
- Weekly/manual external smoke covers LLM, Embedding, RapidOCR, and 11 sources. On 2026-07-21, LLM
  and OCR passed, Embedding failed, six EU sources passed, and SAMR/four FTC pages returned access
  controls (403). Reports omit credentials.

# Published data evidence

`data/evidence/v1` is the reviewable data release. It contains all 11 latest parsed official-source
copies (3,439 sections), a 16-version/3,995-section capture inventory, dataset hashes, sanitized
benchmark metrics, and a local runtime-count snapshot. CI validates the bundle with
`python -m policyguard.scripts.publish_data_evidence --validate`. This makes the GitHub evidence
auditable without committing mutable databases, secrets, provider errors, or machine-local paths.

The release is not active legal truth: nine legal/guidance sources passed structural checks, two
catalog pages are blocked, and all 11 remain without legal-review confirmation. See
`docs/DATA_CARD.md` for exact metrics and limitations.
