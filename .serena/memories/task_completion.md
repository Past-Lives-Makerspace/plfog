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
- Bump `VERSION` in `plfog/version.py` on every PR.
- CHANGELOG in `plfog/version.py`: one entry per member-facing feature per release line. Edit existing entries for fixes/polish to already-unshipped features — do NOT add a new entry. See CLAUDE.md for full rules.
- Discord announce fires automatically on merge when VERSION changes; curate the entry before merging.
