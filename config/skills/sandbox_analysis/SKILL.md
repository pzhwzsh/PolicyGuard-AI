# Sandbox Analysis Skill

This skill requires an explicit reviewer approval for `sandbox:execute`. Execution is disabled by
default and, when enabled, runs in Docker with no network, a read-only root filesystem, dropped
capabilities, process/CPU/memory/time limits, bounded output, and an ephemeral workspace.
