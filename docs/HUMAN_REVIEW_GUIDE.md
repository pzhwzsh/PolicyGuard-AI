# Human review guide

PolicyGuard AI keeps machine-generated development labels separate from real human decisions. The
review packet is `data/evaluation/human-review-packet-v1.json`.

## Current queue

- 9 structurally valid legal or guidance sources pending authorized legal review.
- 120 isolated RAG questions pending section-label review.
- 100 remediation cases pending meaning-preservation review.
- 100 campaign workflow cases pending outcome review.
- Completed human decisions: 0.

## Required reviewer actions

For a RAG item, open the exact official source URL and compare the expected section with the source
text. Choose accept, correct, or reject. A corrected section must exist in the active knowledge
collection.

For a remediation item, confirm that the risky span is correctly located, product facts and units
are retained, the cited section is relevant, and the rewritten copy preserves the original intent.

For a source version, verify authority, jurisdiction, version/effective date, document completeness,
section boundaries, and applicability. Only an authorized reviewer may confirm legal activation.

## Prohibited shortcuts

- Do not set reviewer names or decisions with a script merely to increase sample counts.
- Do not treat an AI source audit as independent human legal review.
- Do not activate catalog pages or a source whose structural review is blocked.
- Do not tune a threshold using the locked test labels and then report it as held-out performance.

## Completion criteria

A metric may be called human-verified only when every included sample has a reviewer alias, timestamp,
decision, and any correction comment. Disagreements should be retained and adjudicated, not silently
overwritten. After review, rerun the benchmark on a frozen test split and publish both successes and
failures.
