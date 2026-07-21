# Git workflow

The repository uses a lightweight feature-branch workflow. The purpose of the graph is to make
engineering decisions auditable, not to maximize the number of colored lines.

```text
main ─────────────●──────────────M────────── tag
                   \            /
feat/memory ────────●──●──●─────
```

Typical lifecycle:

```powershell
git switch main
git pull --ff-only
git switch -c feat/memory-quality-evaluation

# make one coherent change at a time
git add src tests
git commit -m "feat(memory): add contamination evaluator"
git add docs data/evaluation
git commit -m "test(memory): add reviewed recall benchmark"

git push -u origin feat/memory-quality-evaluation
# open PR, let CI pass, review, then merge with a merge commit
```

We retain merge commits for meaningful feature branches. Tiny typo fixes may be squashed. We never
split a single change into artificial commits, backdate commits, or create empty branches for visual
effect.

## Scopes

Recommended scopes are `agent`, `memory`, `rag`, `pdf`, `ocr`, `knowledge`, `workflow`, `api`,
`frontend`, `mcp`, `security`, `ops`, `eval`, `docs`, and `release`.

## Review evidence

Every pull request records:

- the user-visible or architectural outcome;
- tests and measured results;
- security, legal-review, and data-provenance impact;
- migration and rollback considerations.

