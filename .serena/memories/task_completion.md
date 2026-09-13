# Task Completion Checklist

Before a coding task is done:

1. `ruff format . && ruff check --fix .` — format and lint
2. `mypy .` — type check (no errors)
3. `pytest` — all tests pass, 100% branch coverage

## PR workflow
- Create PRs as `HexagonStorms` (not joshplaza): `gh auth switch --user HexagonStorms` first.
- Bot review: use `BOT_PAT` from `.env` (`set -a; source .env`) for PastLivesReviewBot approvals.
- Do PR work in an isolated `git worktree` (other sessions may mutate the primary checkout).

## Versioning
- **Never bump `VERSION`.** It is computed at import by folding `changelog.d/*.toml` over `changelog/base.json`. Do not edit `plfog/version.py`, `changelog/base.json` or `changelog/history.json` in a feature PR.
- Add one fragment: `changelog.d/<pr-number>-<slug>.toml` with `bump` (`patch`/`minor`/`major`), plus `date`, `title`, `changes` for anything members see. Repo tooling and tests take `audience = "internal"` and nothing else. CI fails a PR with no fragment and no `no-changelog` label. See `changelog.d/README.md`.
- One fragment per feature: polish to something still unswept edits that fragment rather than adding a second.
- Discord announce fires automatically on merge, posting the fragments that push ADDED; curate the fragment before merging.
