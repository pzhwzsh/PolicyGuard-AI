# PolicyGuard AI

面向跨境营销内容的证据优先合规审查系统。项目覆盖官方法规持续采集、多法域 RAG、
跨语言检索、PDF 解析、风险定位、保守修改、人工审核、Agent 工作流与 MCP 查询。

系统不会让模型直接下法律结论或发布修改。所有法规更新先进入待审区，只有经过明确的
结构审核和法律审核后才能加入活动知识库。

## 核心能力

- 持续监控中国、美国和欧盟的 11 个官方来源，保存快照、哈希、版本和差异。
- 支持 BM25、Dense、Hybrid RRF、Rerank 和中英文 Query Rewrite。
- 对商品标题、描述和营销声明定位风险，返回对应条款、官方链接和原文证据。
- 生成最小化修改方案、前后 Diff、保留事实检查与修改后复检结果。
- 解析原生 PDF、扫描 PDF、图片和表格；复杂页面可路由至 OCR 或人工审核。
- 使用持久化工作流记录步骤、工具调用、失败降级、人工决策和恢复状态。
- Agent 具有工具白名单、步数和 Token 预算、Prompt Injection 防护及人工兜底。
- 只召回经过人工确认且适用范围一致的案例记忆；法规换版后自动使旧记忆失效。
- 提供 32 个 HTTP API、4 个 MCP 工具和本地管理界面。

## 系统流程

```text
商品文案 / PDF
       │
       ▼
声明提取与文档解析
       │
       ▼
法域、品类、渠道和生效时间过滤
       │
       ▼
BM25 + Dense + Query Rewrite + RRF + Rerank
       │
       ▼
风险声明 ── 对应条款 ── 官方证据
       │
       ▼
人工审核 ── 修改方案 ── 内部草稿 ── 二次复检
```

确定性规则负责高确定性检查，RAG 负责查找可追溯证据，Agent 只处理需要动态选择工具
或存在歧义的步骤。Agent 不具备外部发布权限。

## 真实数据

| 数据项 | 当前数量 |
|---|---:|
| 注册官方来源 | 11 |
| 已抓取版本 | 16 |
| 全部抓取版本解析段落 | 3,995 |
| 各来源最新版本 | 11 |
| 最新版本解析段落 | 3,439 |
| 结构审核通过的法规或指南 | 9 |
| 被阻止激活的目录页 | 2 |
| 已获法律审核确认的待审版本 | 0 |
| 当前活动知识库 | 3 文档 / 13 Chunks |
| 独立人工法律金标 | 0 |

可审计的数据发布包位于 [`data/evidence/v1`](data/evidence/v1)，包括完整来源文本、官方
URL、内容哈希、文件 SHA256、抓取时间、审核状态、评测集清单、模型结果和运行快照。

这些数据证明系统使用了真实官方来源，但不代表已经覆盖完整的全球广告法，也不代表
待审法规已经获得法律认可。详细限制见 [`docs/DATA_CARD.md`](docs/DATA_CARD.md)。

## 实验结果

### 检索模型

30 条困难开发集：

| 模型 | Hit@5 | MRR | 平均延迟 |
|---|---:|---:|---:|
| BGE-small-zh-v1.5 | 0.8667 | 0.6733 | 8.92 ms |
| Multilingual MiniLM | 0.9333 | 0.7972 | 35.25 ms |
| Jina Embeddings v2 Base ZH | 1.0000 | 0.9111 | 30.48 ms |

Jina 加选择性 Query Rewrite 后 MRR 提升至 `0.9444`。改写使用缓存，缓存命中时不再
调用模型。该结果来自开发集，不是生产流量指标。

### 拒答能力

18 条可回答问题与 20 条近域无答案问题的同集校准结果为 F1 `0.9444`、误答率 `0.05`；
但在独立近域集合上的 specificity 只有 `0.25`，因此当前阈值不能直接用于生产。

### Agent 与确定性 Pipeline

| 模式 | 样本 | 成功率 | 平均延迟 | Token |
|---|---:|---:|---:|---:|
| 确定性 Pipeline | 8 | 100% | 约 0.01 ms | 0 |
| Sol Medium Agent | 8 | 37.5% | 13.67 s | 73,100 |

实测表明 Agent 在这组确定性修改任务上成本更高且效果更差，因此默认使用 Pipeline，
Agent 只作为有边界的歧义处理与工具规划能力。

### PDF

当前 PDF 真值只覆盖一份 FTC 官方文档的两页：文本准确率 `0.9714`、标题 F1 `1.0`、
阅读顺序准确率 `1.0`。样本量不足以宣称对所有扫描件或复杂表格均有相同效果。

## 技术栈

- Python 3.12、FastAPI、Pydantic
- SQLAlchemy 2、Alembic、SQLite、PostgreSQL 16
- BM25、FastEmbed/ONNX、Jina Embeddings、RRF、Reranker
- OpenAI-compatible LLM 与 Embedding 接口
- PDFPlumber、PyPDF、RapidOCR，以及可选 MinerU/PaddleOCR/PP-Structure Sidecar
- 官方 Python MCP SDK
- Pytest、Ruff、GitHub Actions

## 快速开始

```powershell
git clone https://github.com/pzhwzsh/PolicyGuard-AI.git
cd PolicyGuard-AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,pdf,mcp]"
Copy-Item .env.example .env
python -m uvicorn policyguard.api.main:app --reload
```

打开：

- 管理界面：<http://127.0.0.1:8000/>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

模型密钥只填写在本地 `.env`，不要提交到 Git。未配置模型服务时，相关能力会明确跳过
或降级，不会生成伪造指标。

## 常用命令

```powershell
# 全量测试
python -m pytest

# 数据证据完整性
python -m policyguard.scripts.publish_data_evidence --validate

# 重新生成本地数据证据
python -m policyguard.scripts.publish_data_evidence

# 检查官方来源更新
python -m policyguard.scripts.check_source_updates

# 启动后台任务 Worker
python -m policyguard.scripts.job_worker

# 启动 MCP Server
python -m policyguard.mcp_server
```

## MCP 工具

- `search_policy`：按法域、品类和渠道查询官方证据。
- `get_workflow`：读取工作流、Checkpoint 和执行事件。
- `create_remediation_plan`：审核接受后生成内部修改计划。
- `create_internal_draft`：二次确认后生成内部草稿。

MCP 不提供任意数据库写入或外部发布工具。

## 质量与边界

- 自动化测试：99 个；本地 98 通过，PostgreSQL 集成测试在 CI 中执行。
- 全包语句覆盖率：69%。
- GitHub CI 验证 Linux、PostgreSQL 16、数据证据哈希、Lint 和完整测试。
- 不自动激活抓取到的法规。
- 不自动发布或覆盖用户原始内容。
- 不把 AI 审核冒充真人法律金标。
- 不将检索命中等同于可回答或合法结论。
- 不构成法律意见，也不保证内容通过平台或监管审核。

## 文档

- [`docs/DATA_CARD.md`](docs/DATA_CARD.md)：数据来源、指标及限制
- [`docs/adr/0001-architecture.md`](docs/adr/0001-architecture.md)：架构与技术选型
- [`POLICY.md`](POLICY.md)：系统约束和禁止行为
- [`DEVELOPMENT.md`](DEVELOPMENT.md)：开发和运行说明
- [`HANDOFF.md`](HANDOFF.md)：详细实现历史与后续工作
