# Chinese query to English law retrieval evaluation v1

Date: 2026-07-21

Dataset: `data/evaluation/rag-cross-lingual-zh-en-v1.json`

Status: **provisional**. The 22 questions and labels passed an AI source-fidelity audit, including
two wording corrections, but they are not human-verified ground truth and must not be presented as
a production or resume metric until the review checklist is completed.

## Scope

- 18 answerable Chinese questions: 8 US FTC and 10 EU UCPD.
- 4 no-answer questions relative to the active four-section US/EU subset.
- Exact jurisdiction filtering is applied before retrieval.
- Top K is 5.

## Measured results

| Retrieval mode | Hit@5 | MRR | Mean latency | P95 latency |
|---|---:|---:|---:|---:|
| BM25 | 0.0000 | 0.0000 | 1.461 ms | 1.784 ms |
| Local Jina Dense | 1.0000 | 0.8889 | 3.609 ms | 4.587 ms |
| Local Jina Hybrid RRF | 1.0000 | 0.8889 | 4.652 ms | 6.833 ms |
| Local Jina + Sol rewrite RRF | 1.0000 | 0.9167 | 12.066 ms | 18.545 ms |

Latency above is a cached-vector local run and excludes the first LLM rewrite. The first Sol medium
rewrite pass used 2 batches, 11,500 tokens, and 42.42 seconds. A cached rerun used 0 model tokens;
provider pricing was not configured, so cost is deliberately reported as unknown rather than
estimated from an invented price.

After the source audit changed two queries, an incremental rewrite run used 4,938 tokens and 18.99
seconds for 2 cache misses while reusing 16 cached entries. Hit@5 and MRR remained unchanged.

All dense modes returned a candidate for all four no-answer questions. Candidate presence rate was
therefore 1.0, proving that retrieval success cannot be used as an answerability or legal decision.

## Model availability

- `jinaai/jina-embeddings-v2-base-zh` ran through local FastEmbed/ONNX.
- Both Jina and `BAAI/bge-m3` returned HTTP 404 through the configured relay because it has no
  `/embeddings` endpoint.
- FastEmbed 0.7.4 on this machine supports the Jina model but not BGE-M3.
- BGE-M3 has no comparable score yet. It remains a candidate, not a measured loser.

## Decision

Keep local Jina as the current cross-language retrieval baseline. Conditional Sol query rewriting is
justified by the provisional MRR increase from 0.8889 to 0.9167, but should remain cached and
selective because Hit@5 did not improve and the first pass consumed 11,500 tokens.

Do not tune an answerability threshold on four negative samples. Expand and human-review the
negative set first, then measure abstention precision/recall separately from retrieval metrics.

## Reproduction

```powershell
python -m policyguard.scripts.benchmark_cross_language
```

For local-only reproduction without probing the configured relay:

```powershell
python -m policyguard.scripts.benchmark_cross_language --skip-remote
```

Raw machine results are written to the ignored `data/benchmarks/` directory. This keeps transient
provider responses and machine-specific timing out of the source history while this document stores
the reviewed result snapshot.

