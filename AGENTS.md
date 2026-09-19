# plfog - Past Lives Makerspace

Django app for membership and studio rental management at Past Lives Makerspace (Portland, OR).

Repo: https://github.com/Past-Lives-Makerspace/plfog

> **Quick orientation:** See [CODEBASE_INDEX.md](CODEBASE_INDEX.md) for the full app map, models, URL structure, and integration overview. Each app also has its own `AGENTS.md` with per-app details.
> **Standards:** See [STANDARDS.md](STANDARDS.md) for coding standards, testing rules and known traps. Read it before changing code.
> **Frontend:** See [FRONTEND.md](FRONTEND.md) for the component library, design system, and rules for building pages.

## Commands

- `pytest` - Run tests
- `python manage.py runserver` - Dev server
- `ruff check .` - Lint
- `ruff format .` - Format
- `mypy .` - Type check

## Testing

BDD/spec style with pytest-describe. Test files named `*_spec.py`. Functions named `it_*` inside `describe_*` blocks.

## Environments

| Environment | Platform | Purpose | DB |
|---|---|---|---|
| **Production** | Render.com | Live app for members | PostgreSQL (via `DATABASE_URL`) |
| **QA / Staging** | Hetzner VPS (`pastlives.plaza.codes`) | Testing before prod | PostgreSQL |
| **Local dev** | WSL2 | Development | SQLite (default) |

Hetzner is **NOT production**. Render is production. Do not confuse these.

## Settings

All configuration via environment variables. See `plfog/settings.py` for available env vars.

## Versioning & Changelog

**A PR adds one file to `changelog.d/` and never touches a version number.** That is the whole
release ritual. `changelog.d/README.md` is the authoring contract; read it before writing one.

```toml
# changelog.d/394-composer-drafts.toml
bump = "minor"          # patch | minor | major
date = "2026-09-13"
title = "The class composer keeps what you typed"
changes = [
  "If the page reloads while writing a class, your next visit offers back what you had typed.",
]
```

`VERSION` and `CHANGELOG` in `plfog/version.py` are **computed at import** from
`changelog/base.json` (the version the fold starts from), `changelog.d/*.toml` (one fragment
per unreleased change) and `changelog/history.json` (every release frozen by the last sweep). The
machinery and the reasoning live in `plfog/changelog.py`.

This replaced a rule where every PR hand-edited the `VERSION` literal at line 5 of a 3,412-line
`plfog/version.py` and inserted an entry at the head of its `CHANGELOG` list. Two PRs open at
once collided at both spots: 15 of the 60 merges before this change — 25% — had to resolve that
file, about 2.4 renumber events a week. **If you find a doc, skill or profile still telling you
to bump `VERSION`, it is stale; fix it.**

- **The version is folded, never written.** Count the bumps in `changelog.d/`, apply them to
  the base. Order-independent by construction, which is what makes a rebase safe — a PR that
  merges late cannot renumber a release that already shipped. Nothing in a PR names a number,
  so nothing in a PR can be stale or collide.
- **A fragment is announced when its own PR merges**, because `release.yml` announces what a
  push *added*. So the window for editing it is before your PR merges, not before the next
  sweep: inside your own PR, refine the fragment rather than adding a second one and the
  combined result goes out once. After it merges the fragment is spent — editing it corrects
  the in-app changelog and announces nothing, so never fold a new bullet into a merged
  fragment expecting members to see it. A fix to something **already live** is its own
  fragment with `bump = "patch"`: members lived with the bug, so it is news.
- **`audience = "internal"` is the tooling release.** `bump` and nothing else — no title, no
  bullets. It moves the version and announces nothing. This used to be a judgement call
  ("do NOT invent an entry to satisfy the rule") and is now a declaration.
- **Entries are plain, member-friendly language** — no jargon, PR numbers, or commit hashes.
- **Only swept history carries version numbers.** A fragment cannot know its own release
  number without merge order, which the tree does not record, so new entries are identified by
  date and the changelog modal renders the version badge only when there is one. Frozen
  entries keep the numbers they shipped under.
- **Sweeping** moves fragments into `changelog/history.json` and moves `changelog/base.json`
  forward. It is deliberate housekeeping, usually right after the release email goes out, and
  it is what keeps a second major's number exact. Nothing breaks if you never sweep: two
  unswept majors are approximated rather than rejected, because the fold runs at app import
  and refusing there would stop the app booting.

### Was this really impossible before?

Issue #358 concluded that `VERSION` had to stay a hand-edited literal because **a workflow
cannot push to `main`**. That half is true and still is: the ruleset on the default branch has
an empty bypass list, and both `GITHUB_TOKEN` and `BOT_PAT` are rejected with `GH013`.

The conclusion drawn from it was wrong. The version does not have to live on the branch — and
`release.yml` had been pushing a **tag** on every single merge, with plain `GITHUB_TOKEN`, the
entire time. Here the version lives on no ref at all: it is a pure function of files on disk.
Issue #365 (the fragments child) declined itself over a cost that only exists if the literal
stays — "a PR would name the version number twice... the fragment's stamp can silently go
stale on a rebase" — so it declined a crippled design rather than this one. **Retest the
premise before you inherit a conclusion from it.**

## Automated PR review

Every pull request marked **Ready for Review** is reviewed by PastLivesReviewBot
automatically, and approved if it has no blockers. Nobody has to ask for it.

- The workflow is `.github/workflows/bot-review.yml`; the rubric it reviews
  against is `.github/bot-review-prompt.md`. **The rubric is the single source of
  truth for what counts as a blocker** — the manual `/pl-bot-review-pr` command
  reads the same file, so change the rubric, not one of the two callers.
- The model writes a verdict file and nothing else;
  `.github/scripts/bot_review_post.py` is what actually posts the review, and it
  is the only thing holding `BOT_PAT`. Its fail-closed behaviour is specced in
  `tests/scripts/bot_review_post_spec.py` — change one, change the other.
- It runs on `pull_request_target` so that PRs from forks are reviewed too. That
  trigger holds secrets, so the contributor's code is never checked out and
  never executed — the change is reviewed as diff text. The header comment in
  the workflow explains the four rules that keep this safe. Read it before
  editing that file.
- **The approval is not a merge.** `main`'s ruleset requires one approving
  review, so the bot's approval is what unblocks the merge, but a human still
  performs it. That gate is deliberate: a push to main deploys to Render and
  fires the Discord announcement.
- **A failed check blocks the merge, approval or not.** The same ruleset
  requires three status checks to pass: `lint` and `e2e` from CI, and
  `fragment` from the changelog workflow. All three finish within about ten
  minutes of a push. The bot reviews as soon as a PR is marked ready, usually
  before those checks finish, so its approval can land on a PR whose e2e run
  then fails; the ruleset is what refuses that merge. The hour-plus `test`
  job is deliberately **not** required: a PR may merge while it is still
  running. A `test` failure after the merge is fixed forward on main.
- Blockers are posted as a **comment**, not a `REQUEST_CHANGES` review, so a PR
  is never stranded behind a blocking review only the bot can dismiss.
  Withholding the approval is already the block.
- It reviews once per PR, on open / reopen / ready-for-review — not on every
  push. To get a **re-review** after fixing something, add the `bot-review`
  label. To opt a PR out entirely, add `no-bot-review`.
- It needs two repo secrets: `CLAUDE_CODE_OAUTH_TOKEN` (from `claude
  setup-token`) and `BOT_PAT`. If a review ever fails to produce a verdict, the
  bot says so on the PR and approves nothing — it fails closed.

## Discord Notifications

`.github/workflows/release.yml` tags the release and posts the announcement, on one trigger,
from one plan. It **announces the fragments the push added** (`git diff --diff-filter=A`
against the tip of main before the push), not the entries matching a version string.

That difference closes the last way a release could go quiet. The old workflow filtered
`CHANGELOG` for entries stamped at the exact current `VERSION`, and an entry stamped at the
wrong number deployed, announced nothing, and went green — indistinguishable from a tooling
release that deliberately carried no entry. The planner now knows which it is, because the PR
said so in its fragment.

Consequences worth knowing:

- **Editing a fragment that already shipped re-announces nothing.** It is not an added file.
  This used to be a rule a maintainer had to hold in their head; it is now a property of the
  diff.
- **A release carrying only `audience = "internal"` fragments tags and announces nothing**,
  without anyone deciding to withhold an entry.
- **A push that adds no fragment moves no version**, so the tag already exists and the run is a
  no-op with a `::warning`. Not a red X: a PR carrying the `no-changelog` label legitimately
  adds none.
- The post is chunked under Discord's 4096-char embed limit and **fails loudly** on a rejected
  post.

### The check that replaced the release guard

`.github/scripts/release_guard.py` is gone. It caught exactly one shape — a push that edited
`plfog/version.py` and left the literal alone — and its own docstring admitted the shape it
could not reach: "a merge that never touches `plfog/version.py` at all does not fail anything...
that is the ordinary forgot-to-bump mistake."

**`.github/scripts/check_changelog_fragment.py` now fails the pull request** that adds no
fragment and carries no `no-changelog` label, and rejects a malformed one. It runs in `ci.yml`
on `pull_request`. That is both earlier and wider than the guard: it catches the mistake the
guard could not see, on a branch nobody has deployed, instead of on main after Render has.

The `#348` shape it replaced is no longer expressible. There is no literal to leave alone.

### Recovering a missed announcement

The release is live and members heard nothing. The lever is `gh workflow run release.yml`,
which re-announces the **newest member-facing fragment** in `changelog.d/`.

**Look before you pull it.** It re-announces whatever is newest, which is right when a post
failed to send and wrong when the post landed and something else went quiet. Re-posting one
members have seen announces the wrong release, and a Discord post cannot be unsent. Nothing may
be owed at all. That judgment is a human's, and there is deliberately no rule here that makes
it for you — three attempts at writing one each produced a different way to double-post.

`python manage.py announce_release` is **not** a companion to the manual run. It sends the
release **email**, but `release.published` is registered on the in-app, email *and* Discord
channels (`core/events/registry.py:638`), so it posts to Discord too — run both and members get
the same release announced twice.

## Standards

Coding standards, testing rules and the traps that have cost real time live in **[STANDARDS.md](STANDARDS.md)**. It is written for every contributor, human or agent. Read it before changing code; the automated reviewer holds pull requests to it.
