# Contributing to PolicyGuard AI

## Branch model

`main` is always reviewable and releasable. Work starts from the latest `main` on a short-lived
branch:

- `feat/<area>-<summary>` for product capabilities;
- `fix/<area>-<summary>` for defects;
- `docs/<summary>` for documentation only;
- `refactor/<area>-<summary>` for behavior-preserving redesign;
- `test/<area>-<summary>` for evaluation and test coverage.

Do not commit directly to `main`. Merge completed branches with a merge commit so that the history
retains the feature boundary. Delete the branch after merging.

## Commit contract

Use Conventional Commits with a meaningful scope:

```text
feat(memory): recall reviewed cases by jurisdiction
fix(rag): reject query rewrites that introduce penalties
test(pdf): add labeled multi-column reading-order case
docs(adr): record memory invalidation decision
```

Each commit should represent one coherent, tested change. Generated caches, credentials, local
databases, uploads, and benchmark scratch output must never be committed. Avoid empty commits and
history manufactured only to make the graph look busy.

## Definition of done

Before a pull request can merge:

1. The change has tests or an explicit reason tests are unnecessary.
2. `ruff check src tests --select E9,F63,F7,F82` passes. Full Ruff cleanup is tracked
   separately until the current style debt reaches zero.
3. `pytest` passes in `APP_ENV=test` without external model credentials.
4. Security and human-review boundaries remain explicit.
5. Documentation and evaluation claims distinguish measured results from planned work.
6. The pull request includes verification evidence and a rollback note.

## Release model

Use annotated tags such as `v0.2.0`. Release notes are derived from `CHANGELOG.md`; versions are
milestones, not arbitrary commit-count targets.
