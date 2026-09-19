# plfog - Past Lives Makerspace

Django app for membership, guilds, classes and studio rental at Past Lives Makerspace (Portland, OR). Repo: https://github.com/Past-Lives-Makerspace/plfog

Read first:
- [STANDARDS.md](STANDARDS.md): coding standards, testing rules and known traps. The automated reviewer holds PRs to it.
- [CODEBASE_INDEX.md](CODEBASE_INDEX.md): app map, models, URLs, integrations. Each app also has its own `AGENTS.md`.
- [FRONTEND.md](FRONTEND.md): component library, design system, page-building rules.

## Commands

`pytest` (tests), `python manage.py runserver` (dev server), `ruff check .` and `ruff format .` (lint, format), `mypy .` (types).

## Environments

| Environment | Platform | DB |
|---|---|---|
| **Production** | Render.com, deploys from `main` | PostgreSQL (`DATABASE_URL`) |
| **QA / Staging** | Hetzner VPS (`pastlives.plaza.codes`) | PostgreSQL |
| **Local dev** | WSL2 | SQLite by default |

Hetzner is **not** production. All configuration is environment variables; see `plfog/settings.py`.

## Versioning and Changelog

**A PR adds one file to `changelog.d/` and never touches a version number.** `changelog.d/README.md` is the authoring contract.

```toml
# changelog.d/394-composer-drafts.toml
bump = "minor"          # patch | minor | major
date = "2026-09-13"
title = "The class composer keeps what you typed"
changes = [
  "If the page reloads while writing a class, your next visit offers back what you had typed.",
]
```

- `VERSION` and `CHANGELOG` in `plfog/version.py` are computed at import from `changelog/base.json`, `changelog.d/*.toml` and `changelog/history.json` (`plfog/changelog.py`). The fold is order-independent, so a rebase can never renumber a shipped release. Any doc still saying "bump `VERSION`" is stale; fix it.
- **A fragment is announced when its own PR merges.** Refine it inside your PR rather than adding a second one. After merge it is spent: editing it corrects the in-app changelog and announces nothing. A fix to something already live is its own `patch` fragment.
- `audience = "internal"` with only `bump` is the tooling release: it moves the version and announces nothing.
- Entries are plain member language: no jargon, PR numbers or commit hashes.
- Only swept history carries version numbers. Sweeping moves fragments into `changelog/history.json` and advances `changelog/base.json`; it is housekeeping, usually after the release email.
- `.github/scripts/check_changelog_fragment.py` (in `changelog.yml`) fails a PR that adds no fragment and has no `no-changelog` label, or that carries a malformed one.

## Releases and Discord

`.github/workflows/release.yml` tags the release and posts to Discord the fragments a push **added**. So editing a shipped fragment re-announces nothing, a release of only internal fragments announces nothing, and a push with no fragment is a no-op with a warning. Posts are chunked under Discord's embed limit and fail loudly.

**A release went out and members heard nothing?** `gh workflow run release.yml` re-announces the newest member-facing fragment. Look first: if the post did land, re-running announces the wrong release, and a Discord post cannot be unsent. That call is a human's. `python manage.py announce_release` is not a companion to it: it sends the email, and `release.published` also posts to Discord, so running both announces twice.

## Automated PR review

PastLivesReviewBot reviews a PR when it is opened (not as a draft), reopened or marked ready, and approves it if there are no blockers.

- The rubric is `.github/bot-review-prompt.md`, the single source of truth for blockers; the manual `/pl-bot-review-pr` command reads the same file. The bot sees the diff only, never the PR description.
- It runs on `pull_request_target`, so the contributor's code is never checked out or executed. Read the header of `.github/workflows/bot-review.yml` before editing it. The model only writes a verdict; `.github/scripts/bot_review_post.py` posts it, holds `BOT_PAT`, and fails closed (specced in `tests/scripts/bot_review_post_spec.py`).
- Blockers are posted as a comment, not a blocking review. Add the `bot-review` label for a re-review, `no-bot-review` to opt out.
- **Approval is not a merge.** `main`'s ruleset requires one approval plus the `lint`, `e2e` and `fragment` checks; a human merges, because a merge deploys to Render and announces on Discord. The hour-long `test` job is deliberately not required; a failure after merge is fixed forward.
- Secrets: `CLAUDE_CODE_OAUTH_TOKEN` and `BOT_PAT`.
