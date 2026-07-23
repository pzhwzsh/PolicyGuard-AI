# PolicyGuard AI 开发指南

## 1. 文档职责

- `README.md`：面向访客，只说明项目是什么、能做什么和如何启动。
- `HANDOFF.md`：当前真实状态、未完成项、边界和下一步。
- `CHANGELOG.md`：按版本记录已经发生的功能变化。
- `DEVELOPMENT.md`：开发、测试、提交和交接规范。
- `docs/DATA_CARD.md`：数据来源、数量、标签性质、指标和限制。
- `docs/adr/`：重要架构选择及其取舍。
- `docs/evaluation/`：模型、检索和数据审核实验的可复现记录。

文档不能重复制造多个“真相来源”。功能历史看 Git/Changelog，当前缺口看 Handoff，
数据结论看 Data Card。

## 2. 开发原则

1. 先定义业务问题、边界和验收指标，再选择框架或模型。
2. 每项 AI 能力必须有确定性基线、固定评测集和失败兜底。
3. 规则、来源、文档、模型、Prompt、Embedding 和索引都必须可追溯到版本。
4. 高风险副作用必须具备幂等、超时、有限重试、审计和人工确认。
5. 指标只能来自可复现实验或真实运行记录，并注明真实、人工、合成或模板生成。
6. 多法域检索必须先过滤法域、品类、渠道、有效期和法律层级。
7. 翻译和 Query Rewrite 只辅助召回；最终证据必须回到官方原文及具体版本。
8. 抓取成功、解析成功、结构审核、法律审核和激活是五个不同状态。

## 3. 代码结构

```text
src/policyguard/
├── api/             HTTP 协议、输入校验、响应映射
├── application/     用例编排、工作流、检索和评测
├── domain/          领域实体、枚举和业务约束
├── infrastructure/  数据库、模型、OCR、向量和外部服务适配
└── scripts/         数据、评测、备份、迁移和运维入口
```

依赖方向保持为 `api -> application -> domain`。基础设施通过接口接入；领域层不得依赖
FastAPI、SQLAlchemy 或具体 Agent 框架。

## 4. 本地环境

复制 `.env.example` 为 `.env`，密钥只保存在本机：

```powershell
python -m pip install -e ".[dev,pdf]"
alembic upgrade head
python -m uvicorn policyguard.api.main:app --port 8002
```

可选 OCR：

```powershell
docker compose -f deploy/parsers/compose.yml up -d rapidocr
```

测试环境禁止调用真实付费模型：

```powershell
$env:APP_ENV='test'
$env:PYTHON_DOTENV_DISABLED='1'
python -m pytest -q
python -m ruff check .
python -m compileall -q src
```

`PYTHON_DOTENV_DISABLED=1` 用于防止测试读取本机 `.env` 中的真实模型配置；需要验证真实
模型或外部服务时，使用单独的受控 smoke test。

## 5. 功能验收门槛

### RAG

- 同时保留可回答、不可回答、近域负例和跨语言问题。
- 对比 BM25、Dense、Hybrid RRF 和 Rerank，而不是只报告最优方案。
- 分开报告召回、排序、拒答、引用和最终回答质量。
- Query Rewrite 必须保留原问题、缓存结果、限制法域漂移，并在失败时安全降级。
- Embedding 缓存键必须包含 provider、model、文档版本和 chunk 版本。
- 无可靠证据必须拒答；候选召回不等于答案成立。

### Agent

- 先证明固定工作流不能合理覆盖该决策，再引入 Agent。
- 工具必须有 Schema、权限、超时、错误码和幂等语义。
- 必须限制步骤数、Token 预算、工具调用和允许的副作用。
- Checkpoint 必须能够在进程重启后恢复。
- 修复建议、内部草稿、人工批准和外部发布是不同权限阶段。
- 未经批准的执行结果不得写入长期记忆；法规变更必须使相关记忆失效。

### PDF/OCR

- 原生文本、扫描件、图片、双栏、多级标题和复杂表格必须走明确的路由。
- 保存原文件、页码、坐标、解析器版本、规范化 JSON 和派生 Markdown。
- OCR 或结构解析失败不得静默吞掉；应进入人工修订队列。
- 合成 PDF 只能验证链路和性能，真实准确率必须来自人工标注的真实文件。

### 法规与修改建议

- 每个违规结论必须指向输入片段、法域、官方来源版本和具体证据。
- 修改应尽量最小化，并保护商品事实、数值、品牌、适用市场和用户原意。
- 修改后必须重新审核并显示残余风险。
- AI 只能给出辅助意见，最终接受或拒绝由人工完成。

## 6. 数据库和外部服务

所有 Schema 变化都通过新 Alembic migration 完成，禁止修改已发布 migration：

```powershell
alembic upgrade head
```

已有本地 SQLite 在迁移前先备份并核对 Schema。PostgreSQL 集成测试由 CI 提供连接；
本地没有 `POSTGRES_TEST_URL` 时允许跳过该单项。

LLM、Embedding、OCR 和官方来源的实时检查属于受控 smoke test，不放入普通单元测试，
不得在日志和报告中泄露密钥或完整提供商错误响应。

## 7. 实验记录

重要实验在 `docs/evaluation/` 单独记录：

```text
问题与假设
基线
唯一变量
数据集与标签来源
模型及版本
结果与成本
失败案例
结论
遗留问题
复现命令和 commit
```

若模型效果没有在同一数据集、同一过滤条件和同一指标下比较，不得声称某模型更优。

## 8. Git 工作流

从最新 `main` 创建单一目的分支：

```text
feat/<capability>
fix/<defect>
test/<evaluation>
docs/<topic>
refactor/<scope>
```

提交信息遵循同样的类型前缀。不要在一次提交里混合模型实验、数据库迁移、界面改造
和无关格式化。不得重写用户已有提交或强推 `main`。

合并前至少完成：

1. 相关测试和完整回归通过。
2. 新行为、失败路径和边界均有测试。
3. `CHANGELOG.md` 已记录本次变化。
4. `HANDOFF.md` 已更新当前状态与剩余工作。
5. 数据、指标或口径变化已同步到 Data Card。
6. commit 中不包含 `.env`、密钥、数据库或用户上传原件。

## 9. 每次更新如何留下记录

一次正式更新应能回答四个问题：

| 问题 | 记录位置 |
|---|---|
| 具体改了什么？ | commit + `CHANGELOG.md` |
| 为什么这样设计？ | commit/PR + ADR 或实验记录 |
| 现在整体做到哪里？ | `HANDOFF.md` |
| 还有什么没做？ | `HANDOFF.md` 的 P0/P1 与边界 |

若这四处与代码不一致，以可运行代码和测试结果为事实，并在本次分支立即修正文档。
