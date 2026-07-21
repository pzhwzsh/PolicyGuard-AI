# Model Configuration

## Current Selection

```env
EMBEDDING_PROVIDER=fastembed
EMBEDDING_MODEL=jinaai/jina-embeddings-v2-base-zh
LLM_MODEL=gpt-5.6-sol
LLM_REASONING_EFFORT=medium
```

Local ONNX evaluation on the same 15 samples:

| Model | HitRate@5 | MRR | Mean latency | P95 latency | Decision |
|---|---:|---:|---:|---:|---|
| `BAAI/bge-small-zh-v1.5` | 1.000 | 0.967 | 27 ms | 51 ms | Recommended |
| `jinaai/jina-embeddings-v2-base-zh` | 1.000 | 0.933 | 117 ms | 203 ms | Slower tie |
| `paraphrase-multilingual-MiniLM-L12-v2` | 0.933 | 0.867 | 50 ms | 82 ms | Weaker Chinese recall |
| `intfloat/multilingual-e5-large` | incomplete | incomplete | - | - | Download failed |

BGE Small was initially selected on the easy 15-sample set. A later 30-sample cross-language hard set changed the decision; see the next section.

## Cross-language hard set

The hard-v1 set contains 30 manually labeled queries against the same official sections. Most CN queries are written in English to test cross-language retrieval, with similar medical, health-food, education, investment, and misleading-advertising sections as distractors.

| Model | HitRate@5 | MRR | Mean latency | P95 latency |
|---|---:|---:|---:|---:|
| `BAAI/bge-small-zh-v1.5` | 0.867 | 0.673 | 8.9 ms | 13.3 ms |
| `jinaai/jina-embeddings-v2-base-zh` | 1.000 | 0.911 | 30.5 ms | 41.2 ms |
| `paraphrase-multilingual-MiniLM-L12-v2` | 0.933 | 0.797 | 35.2 ms | 44.4 ms |

Jina is now selected. It costs about 22 ms more than BGE per cached query on CPU, but recovers every expected section and improves MRR by 0.238. There is no per-token API charge. This is a better quality/cost tradeoff for cross-border retrieval.

Retriever-level results on hard-v1:

| Retriever | HitRate@5 | MRR |
|---|---:|---:|
| BM25 | 0.133 | 0.133 |
| Jina Dense | 1.000 | 0.911 |
| BM25 + Jina Dense RRF | 1.000 | 0.911 |

RRF adds no measured gain on this particular cross-language set because BM25 cannot bridge English queries to Chinese source text. It remains useful for same-language exact legal terms, but the dense result drives this set.

## No-answer calibration

Fifteen out-of-scope questions cover refunds, employment, privacy, customs, patents, tax, shipping, cybersecurity, VAT, and trademarks. With the selected Jina model:

| Group | Count | Top-1 cosine score range |
|---|---:|---:|
| Answerable hard-v1 | 30 | 0.314 to 0.755 |
| Out-of-scope no-answer-v1 | 15 | 0.077 to 0.310 |

The best development threshold is `0.311966`, with answerable recall 1.0 and no-answer specificity 1.0. This threshold is not enabled in runtime because it was selected on the same data used to report the metric and the negatives are relatively easy. A held-out set with near-domain questions is required before enabling abstention.

That held-out test is now complete. Twenty near-domain but unsupported advertising questions scored from 0.091 to 0.608. At the fixed `0.311966` threshold, specificity is only 0.25, meaning 75% of unsupported questions would still be treated as answerable. Examples include prescription-drug public advertising mapping to Article 16 and internet-ad labeling mapping to Article 8. Therefore a cosine threshold alone is rejected. Runtime abstention requires a second evidence-support check that verifies the retrieved section actually entails an answer and can provide an exact quote.

## Evidence-support verifier

An LLM verifier now receives the query plus the top three retrieved sections. A `supported=true` decision is accepted only when the model returns an exact contiguous quote found in those sections. Twenty cases were evaluated in four batches to reduce prompt overhead:

| Model | Decision coverage | Answerable recall | Near-domain specificity | Quote validity | Tokens | Mean batch latency |
|---|---:|---:|---:|---:|---:|---:|
| `gpt-5.6-sol` medium | 1.00 | 0.80 | 1.00 | 1.00 | 27,731 | 13.4 s |
| `gpt-5.6` medium | 0.00 | 0.00 | 0.00 | - | 0 | 7.0 s |

`gpt-5.6` returned 503 for all four batches even after one retry per batch. Sol is therefore selected for this node. The verifier removes the cosine-only false positives in this smoke set, but misses two answerable cases and is expensive. It should be used only after retrieval indicates an ambiguous or near-domain question, not on every request. Batch evaluation saves benchmark cost; runtime user requests remain single-case and need a strict timeout plus fallback to manual review.

## Local Reranker Result

`BAAI/bge-reranker-base` was evaluated on the same 15 samples using BM25 candidates:

| Model | HitRate@5 | MRR | Mean latency | P95 latency |
|---|---:|---:|---:|---:|
| BM25 baseline | 1.000 | 1.000 | not measured here | not measured here |
| `BAAI/bge-reranker-base` | 1.000 | 1.000 | 2830 ms | 4158 ms |

The reranker adds no quality gain on the current easy dataset and adds about 2.8 seconds per query on CPU, so it is not enabled by default. `jina-reranker-v2-base-multilingual` did not finish downloading and has no claimed result. Reranking should be reconsidered only after the evaluation set includes hard negatives and near-duplicate legal sections.

On hard-v1, BM25 candidate recall is only 0.133, so reranking those candidates cannot recover missing evidence: BGE Reranker also scores HitRate@5/MRR 0.133. This demonstrates the candidate-recall ceiling; rerankers improve ordering, not missing candidates.

当前项目没有独立前端配置页，模型配置入口是项目根目录的 `.env` 文件。`.env` 已建立为空模板，真实 API key 只填写在本地。

## 先填哪一组

先填写 Embedding 三项，完成 Dense/Hybrid 检索和模型对比：

```env
EMBEDDING_BASE_URL=https://你的中转服务/v1
EMBEDDING_API_KEY=你的密钥
EMBEDDING_MODEL=服务商支持的模型名
```

推荐第一轮把 `EMBEDDING_MODEL` 填成服务商实际支持的 `BAAI/bge-m3`。这只是第一轮候选，不代表它一定最好；基准脚本会继续测试 `config/model_matrix.json` 中的其他候选。

## 模型候选

| 用途 | 候选 | 什么时候使用 |
|---|---|---|
| Embedding | `BAAI/bge-m3` | 中文、英文和跨市场法规混合检索的第一候选 |
| Embedding | `BAAI/bge-large-zh-v1.5` | 中文法规为主的对照组 |
| Embedding | `intfloat/multilingual-e5-large` | 多语言跨市场对照组 |
| Embedding | `text-embedding-3-small` | 成本和延迟优先的托管模型 |
| Embedding | `text-embedding-3-large` | 质量优先的托管模型 |
| Reranker | `BAAI/bge-reranker-v2-m3` | Dense/BM25 候选召回后的精排 |
| Reranker | `jinaai/jina-reranker-v2-base-multilingual` | 多语言精排对照组 |
| LLM | `qwen-plus` | 中文字段抽取和合规说明 |
| LLM | `deepseek-chat` | 成本敏感的结构化抽取对照组 |
| LLM | `gpt-4o-mini` | 低成本托管对照组 |

模型名称必须以你的中转服务实际支持的名称为准，不要直接照抄候选表。

## 已完成的真实 LLM 对比

使用同一提示词、5 条固定合规样例、`temperature=0`、`reasoning_effort=medium` 测试：

| 模型 | 请求成功 | JSON 合法 | 双字段覆盖 | 原文忠实 | 平均延迟 | 平均 token |
|---|---:|---:|---:|---:|---:|---:|
| `gpt-5.6` | 4/5 | 4/5 | 4/5 | 4/5 | 9395 ms | 255 |
| `gpt-5.6-terra` | 5/5 | 5/5 | 5/5 | 5/5 | 6044 ms | 2833 |
| `gpt-5.6-sol` | 5/5 | 5/5 | 5/5 | 5/5 | 7387 ms | 2035 |

结论：`gpt-5.6` 的 token 低但出现 502，不能作为当前默认；`terra` 质量和速度好但 token 明显更高；`sol` 在这组样例上质量和稳定性与 terra 相同，token 更低，因此当前选择 `gpt-5.6-sol + medium`。这不是大规模结论，后续样本增加后应重新评测。

## Embedding 对比结论

已对 `BAAI/bge-m3`、`BAAI/bge-large-zh-v1.5`、`intfloat/multilingual-e5-large`、`text-embedding-3-small`、`text-embedding-3-large` 各发送一条真实请求。当前中转服务没有可用 Embedding 后端：BGE-M3 连接失败，其余候选返回 503。故目前不能声称任何 Embedding 模型效果最好，也不会把聊天模型当作向量模型。需要提供真正支持 `/v1/embeddings` 的服务后，才能运行项目内的 HitRate/MRR 对比。

## 运行验证

```powershell
cd D:\DeskTop\PolicyGuard-AI
$env:PYTHONPATH="src"
python -m policyguard.scripts.benchmark_embeddings
```

结果会写入 `data/benchmarks/embedding-results.json` 和 `.csv`。没有配置 Embedding 地址或密钥时，命令只输出 `SKIPPED`，不会生成假数据。

配置 Reranker 后再填写：

```env
RERANK_BASE_URL=https://你的中转服务/v1
RERANK_API_KEY=你的密钥
RERANK_MODEL=服务商支持的rerank模型名
```

配置 LLM 后再填写：

```env
LLM_BASE_URL=https://你的中转服务/v1
LLM_API_KEY=你的密钥
LLM_MODEL=gpt-5.6-sol
LLM_REASONING_EFFORT=medium
```

不要把 `.env`、API key 或真实调用日志提交到 GitHub。
