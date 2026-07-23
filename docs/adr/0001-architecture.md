# ADR-0001：总体架构与技术选型

状态：Accepted  
日期：2026-07-19

## Python 3.12

选择原因：AI/RAG生态成熟，类型提示、异步支持和性能足够，且本机已有稳定运行时。

未选 Java：企业后端生态强，但当前项目的模型、检索和文档解析实验以 Python 库为主，跨语言会增加学习噪声。后续高吞吐服务可以独立拆分。  
未选 Node.js：适合前端和 I/O 服务，但主流 RAG 评测、Embedding 和 Agent 实验的 Python 支持更完整。

## FastAPI

选择原因：Pydantic Schema、OpenAPI、异步接口和 SSE 生态适合模型服务，能清晰展示输入输出契约。

未选 Flask：更轻，但参数校验和API契约需要自行补齐。  
未选 Django：后台与ORM完整，但当前服务没有复杂管理后台，框架重量暂时没有收益。

## SQLAlchemy 2 + SQLite/PostgreSQL

开发期使用 SQLite，保证项目零外部依赖即可运行；生产形态使用 PostgreSQL，并通过 pgvector 保存向量和业务数据。

未直接使用 MongoDB：规则版本、检查记录、人工审批与幂等键具有明确关系和事务要求。  
未在 V0 强制 PostgreSQL：会增加首次运行成本，且基线阶段无需向量索引。

## PostgreSQL + pgvector（V1）

选择原因：业务元数据、权限过滤、版本和向量可以在一个事务边界内管理，适合中等规模知识库。

未先选 Milvus：大规模向量检索能力更强，但当前无法证明数据规模需要独立向量基础设施。  
未先选 Pinecone：托管方便，但增加外部成本和供应商依赖，不利于本地复现实验。

## BM25 + Dense + RRF + Rerank（V1）

BM25负责商品编码、法规编号和精确词；Dense负责同义表达；RRF避免直接比较不可校准的两路分数；Rerank用于缩小最终上下文。

不只使用向量检索：精确关键词和编号场景可能退化。  
不一开始加入复杂查询Agent：先建立可测量的检索基线。

### 当前 BM25 实现

V1a 使用项目内可审计的 BM25 实现，中文采用字符单字与双字 Token，参数为 `k1=1.5`、`b=0.75`。

选择原因：语料规模目前只有个位数 Chunk，重点是理解 IDF、长度归一化和指标计算，并形成后续实验基线。  
未先引入 Elasticsearch/OpenSearch：生产能力更强，但当前规模下会把部署复杂度误当成工程深度。  
未先使用结巴分词：领域词典尚未建立，先保留无外部依赖且完全可复现的中文基线。后续必须将分词、字符 Token 和 Dense Retrieval 放在同一测试集比较。

## LangGraph（V2）

选择原因：需要显式State、条件路由、Checkpoint、人工暂停和恢复，图结构比隐式Agent循环更容易调试。

未选纯LangChain Agent：快速，但复杂状态和恢复路径不够显式。  
未选CrewAI：角色协作抽象直观，但当前核心是可靠工作流，不是角色扮演。  
未选AutoGen：多Agent通信能力强，但当前没有证据证明需要通用会话编排。  
未完全自研状态机：可以实现，但会把时间消耗在框架能力上；领域逻辑仍保持框架无关以控制锁定风险。

当前先实现框架无关的工作流状态和事件Checkpoint。选择原因：可以先验证输入、证据、人工复核和失败恢复，再判断LangGraph是否带来实际收益；没有自主决策时不把普通工作流包装成Agent。

## LLM声明提取

工作流通过 OpenAI-compatible `/chat/completions`可选地提取结构化营销声明。Prompt要求JSON数组，服务端再次校验字段和类型；调用失败、超时或非法JSON时回退到标题/描述基线，并写入 `claim_extraction_fallback` 事件。

选择原因：先把模型不确定性隔离在一个节点，保留确定性取证和人工复核边界。  
未让模型直接输出合规结论：声明提取和法律判断是不同责任边界。  
未在未配置时返回模拟AI结果：能力状态必须可由配置和事件轨迹验证。

## Human-in-the-loop幂等

人工审核通过 `decision_id` 建立业务幂等语义。相同decision_id重放返回已有结果，不重复写事件；不同decision_id不能覆盖已经接受或拒绝的终态。

选择原因：HTTP超时、客户端重试和消息重复投递都可能造成重复审核。仅依靠前端禁用按钮无法保证一致性。后续自动修复和外部发布工具也必须复用同类幂等设计。

## 受控修复工具

修复分为“生成计划”和“创建内部草稿”两个阶段，分别使用 `plan_id` 和 `execution_id`。当前工具只保守删除少量演示风险词，不调用LLM，也不写外部平台；原始输入始终保留。

选择原因：建议生成是可逆计算，外部发布是高风险副作用，两者不能共享一次授权。  
未让Agent直接修改商品：模型判断、人工授权和外部写操作需要独立审计。  
未把当前规则修复包装成智能改写：它只是用于验证工具契约、幂等和权限阶段的确定性基线。

## MCP工具边界

使用官方Python MCP SDK提供stdio Server。工具按最小权限拆分为证据读取、工作流读取、修复计划和内部草稿；不暴露任意数据库写入或外部平台发布。

选择stdio：本地Agent客户端需要低配置、可复现的进程通信；公网HTTP MCP另行处理认证、租户隔离和速率限制。  
未把所有Python函数自动暴露成工具：工具描述、参数和副作用必须经过审查。  
未把MCP当作权限系统：调用方身份和最终审批仍需在业务层验证。

## MCP（V2）

选择原因：当检索、OCR、类目验证等工具需要被多个Agent客户端复用时，MCP能提供统一发现和调用协议。

不替代普通函数调用：同进程内部函数不需要网络协议；只有跨进程、跨客户端复用的工具才封装为MCP。

## Redis + 任务队列（V3）

选择原因：OCR、批量索引和Agent任务是长任务，需要脱离HTTP生命周期，支持重试、取消和状态查询。

V0不引入：当前同步基线任务足够短，提前引入会增加部署复杂度且没有可测收益。

## 可观测性与评测优先

每次执行都应记录Trace ID、模型版本、Prompt版本、工具轨迹、Token、延迟、费用和最终人工判断。没有这些数据，就不能声称系统获得提升。

## 多法域适用范围模型

文档与适用范围使用一对多关系：同一官方文档可以适用于多个品类或渠道，不把 `jurisdiction` 等字段硬编码进 Chunk。检索前必须先按法域、品类、渠道和有效期过滤，再进行相关性排序。

未让 LLM 自主判断法域：目标市场是高风险确定性输入，错误路由会使后续证据全部失效。  
未把所有国家文本放进同一个无过滤向量库：语义相似不代表法律适用。  
未将法律、指南和平台政策合并为同级规则：它们的约束力和适用边界不同。

当前覆盖 CN、US、EU，目的是验证架构，不代表已经覆盖三个市场的完整法规体系。美国还存在州法和品类监管，欧盟还存在成员国转化规则及行业专项法规。

## Embedding Provider 与缓存

Dense检索通过 OpenAI-compatible `/embeddings`适配器接入，而不是把某一家模型SDK写进领域逻辑。SQLite缓存按 `chunk_id + provider + model`保存向量，文档内容变化会生成新的Chunk ID。

选择原因：中转 API 和本地/云模型可以替换，同时保留超时、鉴权、响应校验和成本缓存边界。
未默认下载本地大模型：当前机器没有统一Embedding运行时，下载大型模型会引入环境和版本噪声；真实模型应通过固定Provider和评测集验证。  
未在未配置时回退到BM25：两者语义不同，静默回退会造成错误的能力声明。

## RRF混合检索

Hybrid模式使用 Reciprocal Rank Fusion 合并 BM25 和 Dense 的排名，不直接相加两个不可校准的原始分数。这样能保留精确词法命中，同时利用多语Embedding的语义召回；代价是需要执行两次检索并承担更高延迟。

## Rerank边界

Rerank只接收Hybrid的有限候选集（默认最多20条），再返回最终Top-K。它不参与全库召回，也不取代法域、品类、渠道和有效期过滤。

选择原因：Cross-Encoder/Reranker精度高但逐查询成本和延迟高，适合对候选集精排。  
未让Reranker直接扫描全库：会把高延迟误当成准确率提升。  
未把Rerank服务和Embedding服务绑定为一家供应商：两者接口和模型生命周期不同，应能独立替换。
# Controlled Agent context and memory

PolicyGuard separates four kinds of state instead of treating chat history as memory:

- **Working context:** the current product, current retrieved evidence, the latest tool observation,
  tool definitions, and a bounded trace window.
- **Workflow checkpoint:** complete workflow events and Agent traces remain in SQLite for recovery
  and audit, but are not automatically copied into the next prompt.
- **Legal knowledge:** versioned official documents remain the authoritative RAG source.
- **Reviewed case memory:** only a remediation that reached an internally approved draft is stored
  as episodic memory. Recall requires compatible task type, jurisdiction, category, and channel.

`AgentContextBuilder` owns prompt assembly and separate evidence/memory budgets. It recalls at most
three cases and exposes their memory IDs and source run IDs in `context_metadata`. Historical cases
are explicitly non-authoritative: current legal evidence always has higher priority.

Every memory records the active source versions that supported its workflow. Activating a different
version of the same official source invalidates affected memories, so they cannot be recalled until
reviewed again. Agent-generated output never writes itself to memory; the human-confirmed draft
boundary performs the write. This intentionally avoids unbounded conversation history, autonomous
self-learning, and cross-user profiles.
