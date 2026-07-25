# PolicyGuard AI

面向跨境营销内容的证据优先合规审查系统。

系统持续采集不同法域的公开法规与监管指南，通过 RAG 为商品文案定位风险、引用原文
证据，并在人工审核后生成保守修改方案。它不会让模型直接下法律结论，也不会自动发布
或覆盖用户内容。

## 核心能力

- 多法域法规采集、版本管理、变更检测与人工激活
- BM25、Dense、Hybrid RRF、Rerank 与跨语言 Query Rewrite
- 商品标题、描述和营销声明的风险定位与证据引用
- 最小化修改、前后 Diff、事实保留检查与修改后复检
- 原生 PDF、扫描件、图片、表格和复杂布局解析
- 可暂停、恢复和审计的 Agent 工作流
- 工具白名单、Token 预算、失败降级与人工兜底
- 具备适用范围和法规版本失效机制的 Agent 记忆
- 基于已验证商品事实的合规广告文案候选与敏感声明拦截
- 使用真实商品原图生成平台尺寸主图、SKU 图及可选身份保持场景图
- 创意素材人工审批、租户隔离、版本冲突保护与审计素材包
- 商品中心、自动草稿恢复、统一任务中心、容量状态和系统自检
- 上传文件结构扫描、隐私识别、Prompt Injection 拦截和 SSRF 地址校验
- 平台发布前红黄绿总检、场景图身份复核提示和可追溯素材清单
- HTTP API、MCP Server 与本地管理界面
- OIDC/JWKS、租户角色权限、Prometheus/Grafana、OTLP Trace 与安全扫描流水线

## Agent Harness

独立 Harness 层为长任务提供事件溯源、检查点、暂停/恢复/取消、权限审批和预算终止，
不会绕过现有 PolicyGuard 业务流程。上下文按优先级压缩并区分工作、情景和长期记忆；
长期记忆必须经过人工审核。扩展能力包括声明式 Skill、受限 Docker Sandbox、
Streamable HTTP MCP 客户端、最多四个节点的有界多 Agent DAG，以及可复现评测集。

默认配置不会伪装外部能力：Sandbox 默认关闭，MCP 服务列表默认为空。控制台会明确显示
未配置状态，并展示 Token、成本、缓存、工具调用和恢复指标。详细边界和接口见
`docs/AGENT_HARNESS.md`。

## 系统流程

```text
商品文案 / PDF
       │
       ▼
声明提取与文档解析
       │
       ▼
法域、品类、渠道与生效时间过滤
       │
       ▼
混合检索、查询改写与证据校验
       │
       ▼
风险声明、对应条款与官方原文
       │
       ▼
人工审核、修改方案、内部草稿与二次复检
```

确定性规则负责高确定性检查，RAG 负责查找可追溯证据，Agent 只处理需要动态选择工具
或存在歧义的步骤。

## 设计重点

### 证据优先

输出结论必须能回到具体声明、对应条款和来源版本。检索到候选内容不等于问题可回答，
证据不足时系统会拒答或转人工审核。

### 法规版本隔离

新抓取内容只进入待审区。结构检查和法律审核相互独立，未经确认的版本不会进入活动
知识库。法规换版后，依赖旧版本的缓存和 Agent 记忆会失效。

### 受控 Agent

Agent 不能任意调用工具或写入外部系统。工作流限制步骤、工具、Token 和副作用，并记录
每次执行事件。确定性任务默认使用 Pipeline，不为使用 Agent 而使用 Agent。

### 人工闭环

评测标签、法规激活和修改草稿均支持接受、纠正和拒绝。AI 生成内容不会被自动写成
真人审核结果。

## 技术栈

- Python、FastAPI、Pydantic
- SQLAlchemy、Alembic、SQLite、PostgreSQL
- BM25、FastEmbed/ONNX、RRF、Reranker
- OpenAI-compatible LLM 与 Embedding 接口
- PDFPlumber、PyPDF、RapidOCR 与可选解析 Sidecar
- MCP Python SDK
- Pytest、Ruff、GitHub Actions

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,pdf,mcp]"
Copy-Item .env.example .env
python -m uvicorn policyguard.api.main:app --reload
```

模型密钥只填写在本地 `.env`，不要提交到 Git。未配置外部模型时，相关能力会明确跳过
或降级，不会生成伪造结果。

常用验证命令：

```powershell
python -m pytest
python -m policyguard.scripts.publish_data_evidence --validate
```

## 项目边界

- 不自动激活新抓取的法规
- 不自动发布或修改外部商品
- 不把 AI 审核冒充真人法律审核
- 不把检索命中等同于合法结论
- 不保存无边界聊天历史或允许 Agent 自主学习
- 不构成法律意见，也不保证内容通过平台或监管审核

详细的实验数据、数据来源、评测方法和开发记录分别保存在 `docs`、`data/evidence` 和
`HANDOFF.md` 中，避免把项目首页写成实验日志。
