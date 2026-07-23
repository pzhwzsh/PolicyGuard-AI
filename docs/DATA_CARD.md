# PolicyGuard AI data card

Snapshot date: 2026-07-23

## Intended use

This release supports reproducible validation of official-source collection, parsing, retrieval,
bounded Agent execution, and human-review controls. It is not a complete corpus of global
advertising law and is not a substitute for legal advice.

## Published official-source evidence

- 11 registered official sources: CN 1, US 4, EU 6.
- 20 captured versions containing 6,331 parsed sections in total.
- 11 latest source copies containing 5,468 sections.
- 11 latest source copies are blocked from activation. Their manifests predate quality schema v2;
  six EU copies also have generic headings, and temporal metadata requires explicit re-review.
- 0 staged source versions have legal-review confirmation.
- Automatic activation is disabled.
- Active local retrieval remains 3 documents and 13 chunks.

The versioned source copies are under `data/evidence/v1/sources`. Each inventory row includes the
official URL, capture hash, retrieved date, section count, structural status, legal-review status,
and SHA256 of the published release file. Local snapshot paths, credentials, and mutable database
files are excluded.

Reuse terms have not been reviewed source by source. Each released copy is marked
`official_publication_terms_not_reviewed`; confirm the relevant regulator's reuse terms before
redistribution outside this repository.

## Evaluation data

The repository contains 45 authored answerable RAG questions, 35 authored no-answer questions,
22 cross-language questions, six remediation cases, eight Agent cases, ten routing cases, and 180
deterministic robustness variants. These counts overlap and therefore must not be added together as
independent labels.

No sample has independent human legal verification. Cross-language and remediation labels are
explicitly marked `ai_reviewed_pending_human_verification`. Metrics are development evidence only.

`data/evaluation/holdout-v1.template.json` now defines the independent holdout contract. The audit
rejects fewer than 50 samples, development-query overlap, duplicate queries, pending reviews, or an
unfrozen split. The template is not a completed dataset and no holdout metric may be reported until
an independent reviewer supplies and freezes the labels.

The scale suite adds 120 unique synthetic RAG queries, 100 campaign workflows, and 100 unique
remediation inputs. All are deterministic synthetic samples marked `synthetic_pending_human_review`.
The RAG set is explicitly ineligible for legal-quality metrics because templated substitutions can
produce semantically weak questions. It measures plumbing, repeatability, and load behavior only.

## Measured results

On the 30-question hard retrieval set, local Jina achieved Hit@5 1.0 and MRR 0.9111, compared with
BGE-small Hit@5 0.8667/MRR 0.6733 and multilingual MiniLM Hit@5 0.9333/MRR 0.7972. Selective Sol
query rewriting raised Jina MRR to 0.9444 on the same development set.

The same-set abstention calibration reported F1 0.9444 and false-answer rate 0.05, but specificity
fell to 0.25 on the separate 20-query near-domain set. The threshold is not deployment-ready.

Across eight remediation cases, the deterministic pipeline passed 8/8. The bounded Sol Agent passed
3/8, used 73,100 tokens, and averaged 13.67 seconds. The Agent is therefore not the default path.

PDF quality is measured on one official FTC document and two labeled pages: text accuracy 0.9714,
heading F1 1.0, and reading-order accuracy 1.0. This is insufficient for a general PDF claim.

A separate generated layout/load set contains 20 documents and 200 pages with two columns, multi-row
tables, embedded images, and page markers. Marker recall was 1.0 and repeated local parser runs were
approximately 27 ms/page; exact timings are machine-load dependent and retained in the raw report.
It is explicitly not a real-regulation accuracy set.

The real-PDF corpus manifest now records provenance and layout features separately from labels.
The current auditable state remains one real document and one real labeled sample. Ten additional
EUR-Lex PDF candidates are declared in `config/pdf_corpus_sources.json`; the 2026-07-23 collection
attempt was blocked by an AWS WAF HTTP 202 challenge. They are not counted as downloaded samples.

The 100-case deterministic workflow generated 550 events with 0.51 evidence coverage. Serial P95
was 7.534 ms. At eight workers on local SQLite, all cases completed but throughput fell to 44.36
cases/s and P95 rose to 1,142.924 ms, identifying SQLite write contention as the current bottleneck.
Model calls were disabled, so tokens and model cost were both zero for this measurement.

On 2026-07-23, the configured `gpt-5.6-sol` medium endpoint completed the five-case structured-claim
smoke benchmark with 5/5 valid JSON, field coverage, and verbatim faithfulness. It consumed 742
input and 461 output tokens. P50 was 5,463.9 ms and P95/P99 were 6,563.3 ms. This is a small smoke
set, not a model-selection conclusion. Cost remains unreported because the relay price was not
configured; the benchmark now refuses to infer it from unrelated public pricing.

## Operational evidence

The published runtime snapshot is explicitly marked `local_development`; its mutable local rows are
not production-traffic evidence and are not used for quality metrics. The next data milestone is a
consented or public, source-attributed campaign corpus with independent review decisions.

## Reproduction and validation

```powershell
python -m policyguard.scripts.publish_data_evidence
python -m policyguard.scripts.publish_data_evidence --validate
```

Generation requires the ignored local snapshots and benchmark outputs. Validation uses only the
committed evidence bundle and runs in CI. It verifies source-file hashes, portable paths, review
states, dataset truthfulness, and the absence of secret-like fields.

## Known gaps

- No qualified-human-lawyer-verified labels or active staged legal versions.
- All staged source manifests require a quality-schema-v2 structural and temporal re-review.
- No production or consented customer traffic.
- No independent cost figure because provider pricing is not configured.
- BGE-M3 remains unmeasured in this environment.
- PDF tables, scans, images, and multi-column layouts lack a reportable evaluation sample.
- The independent 50-100 item human-reviewed holdout has tooling but no completed labels.
- The declared official PDF candidate endpoint is currently blocked by an AWS WAF challenge.
- The 20-document complex PDF set is synthetic and cannot close the real-document accuracy gap.
- Coverage is bounded to selected CN/US/EU advertising-related sources.
