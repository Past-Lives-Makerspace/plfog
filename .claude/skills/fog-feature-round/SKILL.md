---
name: fog-feature-round
description: >-
  Ship a LARGE plfog feature round — multiple related features, each its own PR — sequentially all the way
  to production: dependency-ordered branches off main, one persistent builder subagent per feature, an
  independent adversarial reviewer per PR with a fix round and delta re-review, PastLivesReviewBot approval,
  manual squash-merge gated on e2e, deploy verification, and post-deploy config flips. Use when the user says
  things like "proceed to the next phases", "build all of these", "take this whole round to production", or
  hands over several agreed features at once. For a single ticket use /drive instead. This skill merges to
  production repeatedly on its own — invoke it only when the user has handed over the whole pipeline.
---

# fog-feature-round — sequential multi-PR shipping for plfog

This skill encodes the pipeline that shipped the 2026-08-26 six-feature round (v1.9.6 → v1.14.0: event
delete, refunds + Payments panel, roster/waitlist, guild subscriptions, orienter availability, paid
orientations) with zero production incidents. The value is in the sequencing discipline and the recorded
failure modes — follow both.

## Preconditions

- Each feature in the round is already agreed and written down, one per feature, with acceptance
  criteria and an out of scope list. `/drive`'s beat 1 is the shape to write them in, and the UI
  checklist at `.claude/skills/drive/references/ui-checklist.md` is the bar each one clears before a
  builder sees it. Older rounds took these as spec docs under `docs/superpowers/plans/`; a GitHub issue
  per feature is better, because CI moves its board card.
- The user has explicitly said to build and merge. This skill merges to production; do not infer that.
- You are in the PRIMARY checkout (`~/Code/plfog`). **No worktrees, no parallel builds** — specs in one
  round overlap in `hub/views.py` and `membership/models.py`; parallel builders in one tree corrupt each
  other. Sequential is the design, not a fallback. (The old third reason, concurrent PRs colliding on
  `plfog/version.py`, is gone: #394 moved the version into folded `changelog.d/` fragments.)

## Phase 0 — Orient and order

1. `git checkout main && git pull --ff-only`. If untracked plan files collide with incoming commits,
   diff them (another session may have committed identical copies) before moving anything aside.
2. Nothing to read and nothing to reserve: since #394 no PR names a version number. The version is folded
   at import from `changelog/base.json` plus the bumps in `changelog.d/`, so a concurrent round cannot
   take "your" number and a rebase cannot leave a stale one behind.
3. Build the dependency order: providers before consumers (e.g. a refund engine before anything that
   refunds; a data-model change before the feature that prices it). Ship the smallest independent PR first
   as a pipeline warm-up. Record the order in TaskCreate tasks with `addBlockedBy`.

## Per-PR loop (repeat for each feature, strictly in order)

### 1. Branch + spec commit
`git checkout -b fog/<slug>` from fresh main. Commit the spec doc alone first
(`docs: spec for <feature>`). Every commit message ends with the Claude-Session trailer; **no dashes in
commit messages** (Jo's rule for copy-ready artifacts).

### 2. Builder subagent (persistent — this matters)
Spawn ONE `claude`-type agent per feature to implement the spec. Its prompt must include:
- The spec path as single source of truth; implement all phases EXCEPT the changelog fragment.
- Read `AGENTS.md`, `STANDARDS.md` + `FRONTEND.md` first; verify cross-spec contract names against the actual tree
  (specs go stale the moment a sibling PR merges).
- **Do NOT commit, do NOT push, do NOT switch branches** — the orchestrator owns git.
- Environment facts (copy these verbatim into the prompt):
  - Tests: `.venv/bin/pytest <paths> -q`; judge by pass/fail lines — the global coverage gate ALWAYS
    fails on partial runs and means nothing there.
  - `DATABASE_URL="sqlite:///tmp-check.sqlite3"` for `manage.py check` / `makemigrations` (delete after).
  - `ruff format` generated migrations too (the pre-push gate rejects unformatted files).
  - The pre-push hook runs REAL mypy — full annotations, `stripe.params` TypedDicts for Stripe params.
  - Single-line `{# #}` comments only; run `tests/template_comment_lint_spec.py`.
  - No dashes in member-facing copy. The changelog renders into every hub page — avoid template marker
    strings that collide with negative test assertions.
- Builders checkpoint multiple times while long regression runs finish; the final report may arrive on
  the third or fourth task-notification. **Do not act on interim checkpoints.**

### 3. Orchestrator verify + commit
Spot-run the new spec files + template lint yourself, `ruff check .`, `manage.py check`. Then
`git add -A` and **`git reset` the other features' plan docs and any stray files** (mock PNGs, compose
override, `.playwright-mcp/`) before committing. One feature commit, plain-language body.

### 4. Changelog fragment (orchestrator-owned, the announce fires on merge)
Add ONE file to `changelog.d/` per PR and **never touch `plfog/version.py`** — `changelog.d/README.md`
is the authoring contract. `bump = "minor"` for a net-new feature, `"patch"` for a fix to something
already live, `audience = "internal"` (bump only, no title, no bullets) for tooling members never see.
Traps that have bitten:
- **The entry text renders on every hub page** — a phrase like the name of a removed button trips
  negative assertions repo-wide. Re-run `tests/plfog/` plus one page-level spec after writing it.
- The entry must describe reality (a reviewer caught "guild pages nudge staff" when the nudge lived on
  the dashboard). No dashes, plain ELI14 language.
- **Refine the fragment inside your own PR, never add a second one.** It is announced when its own PR
  merges, so the editing window closes at merge, not at the next sweep.

### 5. Push through the gate
`git push` runs ruff check/format + real mypy. A failure here is a REAL finding — fix it (typed Stripe
params, format the migration), amend, re-push. Never bypass the hook.

### 6. PR as HexagonStorms, ALWAYS as a draft
**Re-check `gh auth status` every single time** — concurrent sessions flip the active account
(bit this round twice). `gh auth switch --user HexagonStorms` then `gh pr create --draft` with a body
that states what shipped, the spec path, and the real test evidence.

**`--draft` is not optional.** Jo merges on sight of the bot's approval without re-reading the PR
("if I see approved I'm merging it"), and he batch-sweeps open PRs. On 2026-09-18 that caught work
mid review twice: #439 merged carrying a false changelog claim, and #445 merged at the commit before
a reviewer-confirmed regression was fixed. Both announced to Discord on the way out, which cannot be
taken back, and both had to be fixed forward.

The bot reviews on open / reopen / **ready for review**, so a draft carries no approval and nothing
to merge on. Mark it ready with `gh pr ready <N>` only after step 8's delta re-review returns APPROVE.
That is what makes the approval mean "safe to merge" rather than "this PR exists".

### 7. Independent adversarial review (fresh agent per PR)
Spawn a NEW `claude`-type reviewer: read the diff via `gh pr diff <N>`, review against STANDARDS.md +
FRONTEND.md, **attack the domain** (money orderings, permission edges via crafted-POST probes, state-machine
races, N+1 claims — tell it exactly which orderings to construct), spot-run up to ~5 spec files, report
verdict + numbered findings with file:line. **It must never edit files or post to GitHub.**
This pipeline's reviews found real blockers in 5 of 6 PRs — do not skip or soften this step.

### 8. Fix round → delta re-review (same agents, by SendMessage)
- Send findings to the ORIGINAL builder (it has full context); orchestrator fixes its own commits
  (fragment wording, one-line selector fixes) directly.
- Commit + push the round, then SendMessage the ORIGINAL reviewer to delta-verify each finding against
  the pushed commit. Repeat until APPROVE. Reviewers sometimes find the fix introduced a new variant
  (a selector matching two elements instead of zero) — the delta pass is not a rubber stamp.
- **Only once the reviewer returns APPROVE, `gh pr ready <N>`.** Until then the PR stays a draft and
  the bot never sees it. Marking ready with a known open blocker defeats the whole arrangement,
  because ready is the signal Jo merges on.

### 9. Bot approval + merge (manual — repo auto-merge is DISABLED)
- `set -a; source .env; set +a` then approve as PastLivesReviewBot with `GH_TOKEN="$BOT_PAT"`
  (see `reference_plfog_pr_ops` memory for the exact form and its classifier fallback).
- Wait for the **e2e** check in a background poll loop (~3 min; the `test` job is the ~1 h mutation run —
  waiting for it is optional but right for money PRs; the user may merge earlier at their call).
- `gh pr merge <N> --squash`. A merge whose push **adds a member-facing fragment auto-fires the Discord
  announce** (`release.yml` diffs for added files), so the fragment must already be right. A push adding
  no fragment moves no version and is a no-op with a `::warning`, not a red X.

### 10. Close the loop
- `git checkout main && git pull --ff-only`; mark the task complete; cut the next branch from fresh main.
- If a later branch was stacked pre-merge, rebase with
  `git rebase --onto origin/main <old-parent-tip> <branch>` — a plain rebase replays the pre-squash
  commits and conflicts.
- Post-deploy config: watch `https://members.pastlives.space/accounts/login/` for the new version badge
  in a background loop, THEN flip any prod settings via
  `DATABASE_URL="$PROD_DATABASE_URL" .venv/bin/python manage.py shell -c ...` — print the connected host
  before writing and read the value back after (verify by data, never by exit code).

## Failure modes this pipeline already survived (check for them)

- **Pre-existing red on main**: a parity spec (e.g. `core/spec/scheduled_jobs_spec.py`) failing before
  your first PR. Diagnose whether the dispatcher is registry-driven before "fixing" the expectation —
  then fold the fix into your next PR and say so in its body. (Hit AGAIN in the 2026-08-27 round: the
  hand-kept `_DISPATCHER_ALWAYS` tuple was missing `expire_orientation_payment_holds` from the prior
  paid-orientations round — `run_scheduled_tasks` iterates `SCHEDULED_JOBS`, so the job WAS dispatched;
  the fix is one line in the spec's tuple. This parity spec goes stale every round that adds a job.)
- **Registering a scheduled job** requires updating that same parity spec's cadence tuples.
- **The `e2e` CI job is a SEPARATE lane** (`pytest -m e2e`) that the builder's normal `pytest <paths>`
  run DESELECTS — so a UI relocation/rename can pass every unit/integration spec and still break a
  Playwright e2e that navigates the old path. Any feature that MOVES or RENAMES a surface (a tab, a
  button label, a section) MUST grep `tests/e2e/` for the old string and run the affected e2e spec
  locally (`.venv/bin/pytest tests/e2e/<file> -m e2e --no-cov -o addopts="" -q` — Playwright runs in
  this WSL env). Do this in orchestrator verify, BEFORE pushing; otherwise the e2e gate catches it after
  the PR is up and you burn a fix+re-review+re-approve cycle (bot approval is dismissed by the new commit,
  so re-approve on the new head before merging). The 2026-08-27 round's Orientations-tab move broke
  `tests/e2e/orientation_booking_spec.py` (clicked "Guild Calendar", section had moved to "Orientations").
- **Coverage/mutation nit on a merge-on-e2e PR**: if a reviewer flags an uncoverable branch (e.g.
  `if form.is_valid():` on a form that can never be invalid), fix it BEFORE merging — you merge on the
  e2e gate, not the ~1 h mutation `test` job, so a surviving mutant would turn main red post-merge. The
  clean fix for an always-valid single-field form is `form.is_valid(); form.save()` (no branch; `save()`
  raises loudly on the impossible invalid case).
- **Port 8000 occupied** for the local stack: add a temporary `ports: !override` to the gitignored
  `docker-compose.override.yml`; never kill another project's container.
- **Screenshots/manual QA**: dev DB seed data lacks Stripe ids and real dates — stamp/restore local data
  explicitly, and REVERT fake Stripe ids afterward.
- **Safety-classifier-unavailable notes** on a subagent's work → do your own extra verification pass
  before shipping it.
- **Decorator capture**: a helper `def` inserted between `@login_required` and its view silently steals
  the decorator. Watch insert positions in `hub/views.py`.
- Builders that "hold for the suite" across several notifications are fine; a builder that spins with no
  new evidence should be taken over — run the verification yourself and release it.

- **A builder subagent cannot run a browser.** For any feature with a non-trivial JS
  runtime (the 2026-08-27 auto-navigating guided-tours segment player), the builder's
  "all green" covers ONLY pytest — it never exercised the JS. The orchestrator MUST run
  the feature's Playwright e2e locally before pushing; that round's e2e surfaced THREE
  real runtime bugs the builder shipped blind (Driver.js labels the last step of each
  per-page instance "Done" so a single-step segment ends the tour early — set the label
  at config time, not a post-render override; a synthetic hx-boost anchor click does not
  reliably fire htmx navigation and strands the tour — use `window.location.assign`; a
  `keepalive` state POST is deprioritized by the browser and lands late — drop it, no
  unload happens). Judge the JS by the e2e, never by the builder's pytest pass.
- **Browser-writing e2e flakes on local SQLite, not on Postgres.** A `live_server` e2e
  where the browser POSTs while the test reads the DB hits SQLite's single-writer lock
  (`database table is locked` -> 500 -> stale read -> intermittent fail, ~30%). It is a
  local artifact; CI runs e2e on Postgres (MVCC) where it is stable. Reproduce/verify the
  CI way: `DATABASE_URL="postgres://plfog:plfog@localhost:5433/plfog" .venv/bin/pytest
  tests/e2e/<file> -m e2e --no-cov -o addopts="" -q`. Switch to Postgres before assuming
  a product bug (see `reference_plfog_e2e_sqlite_flake` memory).

## What NOT to do

- No parallel builders in one tree; no worktrees for this flow.
- Never predict a pending agent's result or act on a checkpoint as if it were the final report.
- Never merge with an unreviewed diff, and never let the reviewer be the builder.
- Never edit prod data without printing the connected host first.
- Don't inflate: a one-line fix found in review is yours to make directly; don't spin up an agent for it.
