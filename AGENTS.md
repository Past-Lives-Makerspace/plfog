# plfog - Past Lives Makerspace

Django app for membership, guilds, classes and studio rental at Past Lives Makerspace (Portland, OR).

Read before changing code:
- [STANDARDS.md](STANDARDS.md): how code is built and tested here, and the traps that have cost real time. The review bot holds every PR to it.
- [CONTRIBUTING.md](CONTRIBUTING.md): before writing an issue or a PR description; CI checks PR descriptions against it.
- [CODEBASE_INDEX.md](CODEBASE_INDEX.md): the app map. Each app's own `AGENTS.md` carries its detail.
- [FRONTEND.md](FRONTEND.md): before touching a template, CSS or a page.

## Environments

Production is **Render**, deployed from `main`. Staging is the Hetzner VPS (`staging.pastlives.space`, a contained clone of production; `deploy/staging/README.md`). Local dev defaults to SQLite. Configuration is environment variables, read in `plfog/settings.py`.

## Releases

A PR ships one fragment in `changelog.d/` and leaves every version number alone; the version is folded from fragments at import. `changelog.d/README.md` is the contract for writing one, refining it and knowing when it is spent.

Merging to `main` deploys to Render and `release.yml` announces the fragments that push **added** on Discord, so a merge is always a human's call. When a release went out and members heard nothing, the one lever is `gh workflow run release.yml`, which re-announces the newest member-facing fragment; check first that the post really is missing, because a Discord post cannot be unsent. `python manage.py announce_release` sends the release email and also posts to Discord, so it is a separate announcement, never a retry.

## Review and merge

PastLivesReviewBot reviews a PR against `.github/bot-review-prompt.md` (the single source of truth for blockers) when it is opened as ready, reopened or marked ready, and approves it when nothing blocks. It reads the diff only. The `bot-review` label asks for a re-review; `no-bot-review` opts out.

`main` merges on one approval plus the `lint`, `e2e`, `fragment` and `description` checks. The hour-long `test` job runs alongside; a failure there is fixed forward on `main`.

Before editing `.github/workflows/bot-review.yml`, read its header: it runs with secrets on fork PRs and stays safe only by reviewing the diff as text.

## Agent skills

### Issue tracker

GitHub issues on this repo, written in the Ticket form's shape and placed on the kanban board; `/create-issue` files one. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles use labels of the same name; `bug`, `enhancement` and `wontfix` already exist here. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` is the glossary and `docs/adr/` holds the decisions. See `docs/agents/domain.md`.
