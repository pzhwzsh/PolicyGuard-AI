# Agent Harness

PolicyGuard keeps its business-specific `ControlledAgent` and compliance workflow, and adds a
separate general-purpose Harness for infrastructure experiments. The Harness does not gain product
approval or publishing authority.

## Runtime contract

Each run has a revisioned manifest, append-only JSONL event stream, immutable checkpoints, bounded
plan, explicit budgets, permission grants, context metrics, and terminal result. Mutations require
the caller's expected revision. A cross-process lock file prevents concurrent writers; stale input
is rejected instead of silently overwriting state.

Supported lifecycle:

```text
ready → running → ready → completed
          │          │
          ├─ tool failure → paused → resumed
          └─ permission required → paused → approved

ready/running/paused → cancelled
budget exhausted     → failed
```

SSE exposes typed events such as `step.started`, `tool.completed`, `tool.failed`,
`permission.required`, `checkpoint.saved`, and `run.completed`. The stream can start after any
event sequence, making reconnect and replay deterministic.

## Context and memory

Context items carry a kind, priority, source, and pin flag. The manager reserves output capacity,
caps tool results, compresses lower-priority items deterministically, rejects pinned-content
overflow, and records per-kind tokens, dropped items, saved tokens, prefix hash, and cache hits.

Memory has three explicit tiers:

- working: transient run state;
- episodic: prior execution observations;
- long-term: reviewer-confirmed information only.

No run may write unreviewed long-term memory.

## Skills, MCP, and sandbox

Skills are declarative manifests with semver, tool references, permissions, a bounded plan, and a
required `SKILL.md`. Discovery never dynamically imports skill code. Unknown permissions,
unavailable tools, undeclared step permissions, symlinks, and duplicate names are rejected.

The MCP Client registry validates HTTPS destinations, supports host allowlists, bounded retry,
authentication headers, response-size limits, remote-error normalization, and tool ownership.
Tool-name collisions across servers fail registration.

Code execution is disabled by default. When explicitly enabled, the Docker executor uses no
network, a read-only root filesystem and input mount, dropped Linux capabilities,
`no-new-privileges`, tmpfs scratch space, CPU/memory/PID/time/output limits, and an ephemeral
workspace. The Agent must pause for `sandbox:execute`; only a reviewer can grant it.

## Multi-Agent boundary

Multi-Agent orchestration is a bounded DAG rather than an open conversation. It supports at most
four agents, structured versioned messages, dependency validation, deterministic scheduling,
shared message and Token budgets, and cycle rejection. The current demo runner is deterministic;
model-backed roles must use the same budget and event contracts before production use.

## Evaluation

`data/evaluation/harness-v1.json` is a development control-flow set, not a legal-quality holdout.
The runner reports task success, tool precision/recall, recovery signals, latency, Token usage, and
cost. Failure-injection tests cover permission denial, stale revisions, tool exceptions, resume,
budget exhaustion, MCP collisions, cyclic DAGs, and sandbox isolation flags.
