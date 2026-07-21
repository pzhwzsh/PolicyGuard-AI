# Cross-language source fidelity audit v1

Date: 2026-07-21

Reviewer type: AI source audit. This is not human legal annotation.

## Sources checked

- FTC `ftc-truth-evidence`: advertising claims must be truthful, not deceptive or unfair, and
  evidence-based; specialized products or services may have additional rules.
- FTC `ftc-specialized-products`: specialized products or services may have additional,
  category-specific requirements beyond general truth-in-advertising standards.
- UCPD Article 6: false or deceptive information affecting a transactional decision, including
  product nature, availability, benefits, risks, composition, quantity, origin, expected results,
  tests, commitments, and price calculation.
- UCPD Article 7: omission, hiding, unclear, unintelligible, ambiguous, or untimely presentation of
  material information, and undisclosed commercial intent.

## Positive-label audit

| Samples | Expected section | Result |
|---|---|---|
| `us-pos-01` to `us-pos-05` | `ftc-truth-evidence` | Aligned after revising `us-pos-04` from the stronger “performance guarantee” wording to an evidence-based efficacy claim. |
| `us-pos-06` to `us-pos-08` | `ftc-specialized-products` | Aligned after revising `us-pos-07` from an inferred professional-services category to the source's generic specialized-service scope. |
| `eu-pos-01` to `eu-pos-05` | `eu-ucpd-article-6` | Aligned with the enumerated misleading-action elements and transactional-decision condition. |
| `eu-pos-06` to `eu-pos-10` | `eu-ucpd-article-7` | Aligned with omission, hiding, clarity, timing, and commercial-intent conditions. |

No positive query introduces a fixed penalty, new effective date, approval requirement, or actor
not present in its target section after the two revisions.

## No-answer audit

The imprisonment term, advertising tax rate, fixed EU fine, and trademark form questions are absent
from the active four-section evaluation subset. Their labels mean `not_answerable_from_active_subset`,
not that no US or EU authority contains an answer.

## Result

- Reviewed: 22
- Accepted without change: 20
- Revised for source fidelity: 2
- Human verified: 0

The dataset status is `ai_reviewed_pending_human_verification`. A person must still review every
sample before the results can be called human-labeled or used as a final resume metric.
