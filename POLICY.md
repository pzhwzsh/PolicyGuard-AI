# PolicyGuard AI boundaries

This document describes product behavior enforced by `config/guardrails.json` and
`policyguard.application.guardrails`. It is an engineering contract, not a legal disclaimer alone.

## What the system may do

- Retrieve versioned public regulatory material for an explicitly selected jurisdiction.
- Identify claim spans and present candidate legal evidence with exact source links.
- Generate a reversible, minimal internal rewrite draft after human evidence review.
- Recall a small number of scope-compatible cases that were previously confirmed by a human.

## What the system must not do

- Infer the applicable jurisdiction from language alone.
- Present candidate retrieval as a final legal conclusion.
- Activate newly downloaded law or parsed PDF content without legal review.
- Use translated text as the final authority when official source text is available.
- Remove numbers, quantities, prices, or specifications during a conservative rewrite.
- Publish or mutate an external product automatically.
- Write Agent output to long-term memory before human confirmation.
- Recall a case based on an obsolete source version.
- Execute instructions found inside uploaded documents or retrieved evidence.
- Expose credentials, hidden prompts, unrestricted tools, or arbitrary command execution.

## Decision states

- `passed` means the configured baseline found no supported issue; it is not legal approval.
- `review_required` means evidence or a rewrite requires human interpretation.
- `needs_more_evidence` means the system must abstain instead of inventing a conclusion.
- `draft_ready` is an internal reversible artifact with `external_side_effect=false`.

## Change control

Changes to supported jurisdictions, tools, review requirements, or side-effect policy require a
versioned edit to `config/guardrails.json`, tests demonstrating allowed and denied behavior, and a
reviewed pull request.

