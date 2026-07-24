# Postmortem: small Harness runs failed before their first tool

Date: 2026-07-24
Status: fixed and covered by regression tests
Impact: development-only; no user or production run used the new Harness

## Incident

The first failure-injection run used a 1,000-token budget and a two-item pinned context. It failed
inside context assembly before calling the tool:

```text
HarnessRuntime.advance
  → HarnessRuntime._assemble_context
  → ContextManager.assemble
  → ValueError: context_pinned_items_exceed_budget
```

The objective and input together used far fewer than 1,000 tokens, so the rejection contradicted
the visible budget and prevented the Agent from making progress.

## Root cause

`ContextBudget` reserved a fixed 2,000 tokens for model output. With a total budget of 1,000,
`input_limit` correctly clamped to one token. The runtime reused that default for every run instead
of scaling the output reservation to the selected run budget. The second pinned item therefore
looked larger than the remaining one-token input capacity.

The failure belonged to Harness configuration composition, not the tokenizer or compression
algorithm. Increasing the test budget would have hidden the bug and left small-budget runs broken.

## Fix

The runtime now calculates output reservation as:

```text
min(2,000, max(64, max_tokens / 4))
```

The same calculation is used for input assembly and tool-output accounting. A 1,000-token run now
receives a 750-token input capacity and a 250-token output reservation. The global context manager
still rejects genuinely oversized pinned content.

## Validation

Regression coverage now proves that:

- a 1,000-token run executes its first tool;
- checkpoints are saved after the tool, pause, and completion;
- a permission-gated tool pauses before execution;
- an injected first-call timeout creates `tool.failed` and a checkpoint;
- resume retries the same step and completes without duplicating the previous tool call;
- stale revisions remain rejected;
- Context compression still stays under the input limit and reports saved tokens.

This incident is intentionally retained because it demonstrates why budget accounting must be
tested at small boundaries instead of only with production-sized context windows.
