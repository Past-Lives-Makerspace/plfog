---
name: drive
description: >-
  Take one plfog ticket from an idea to an open, bot approved pull request the way a company would: a product
  owner writes the ticket with acceptance criteria and an out of scope list, a senior engineer posts a plan
  and then builds against it, and every criterion is handed back with evidence. Use when the user types
  /drive, hands over a GitHub issue number, or describes one feature they want built. Stops at the PR,
  because the merge deploys to production and announces to members. Replaces RPIPO and fogstorm.
---

# /drive — product owner to engineer, one ticket at a time

    /drive 412                              # an existing GitHub issue
    /drive admins should be able to bounce a submission back   # no issue yet: beat 1 writes one

## Who is who

| Role | Who plays it | Owns |
|---|---|---|
| **PO / UX** | **you, this session.** No agent. | The ticket: criteria, out of scope, UX decisions. The only role that talks to Jo. |
| **Senior engineer** | **one persistent agent**, continued by `SendMessage` | The plan, the build, the evidence. Owns technical approach; never product scope. |
| **Code review** | **PastLivesReviewBot**, free, automatic on every PR | Is the code any good. |
| **Second reviewer** | a fresh agent, **only** when the trigger below fires | Does the diff do what the ticket said. |
| **Jo** | | Approves the ticket. Merges. |

Three writings, each capped at one screen and all of them on GitHub: **the issue**, **one plan comment on
it**, **the PR body**. Nothing else is written down. The issue is the engineer's input, the reviewer's
rubric and the PR body, so it is written once and read four times.

**The handoff is the whole interface.** The engineer receives the ticket and nothing else: not your greps,
not your reasoning, not the transcript of how you filled it in. If the ticket is too thin to build from,
that is a visible bug in the ticket, which is the point. Carry the expensive findings across as the five
`Facts` bullets, never as a transcript. Agent reports back are capped at 200 words; the artifact is on
GitHub, so the report is a pointer, not a copy.

## The risk trigger, used twice

A ticket is **risky** when it does any of:

- **real DDL** — a column, table or index added, dropped or altered. A choices only `AlterField` is **not**
  DDL: no schema change, self reverting, invisible to the running database. Grade the migration by what it
  does to the schema, never by the word "migration".
- **money**, **auth and permissions**, or **member wide email or Discord**.

Risky does two things and nothing else: the plan waits for Jo at beat 2, and a second reviewer runs at
beat 4. A diff over **800 changed lines** also earns the second reviewer. There are no tiers.

## How you talk to Jo

Every beat ends with one status block and nothing else. No narration, no summary paragraph, no explaining
your approach. If Jo wants the reasoning he will ask for it, and then you give it.

```
#412 Bounce a submission back · Eng building · 1 agent · 41 min

  AC   Should be                            Is now
  ✅1  Admin can send a submission back     done, 3 specs
  🔨2  The bounce asks for a reason         building
  ⏸3  Instructor gets the reason by email  not started

  Blocked on: nothing
```

`Should be` is the criterion in Jo's words. `Is now` is the truth at this moment. It is never aspirational
and never a guess: unchecked reads `unchecked`. The header carries the running cost as it accrues, never on
request.

## The budget — count these out loud and stop when one runs out

| Limit | Number | On hitting it |
|---|---|---|
| Agents, whole ticket | **2** (3 only if a scout was needed at beat 1) | Stop. Say what the next one would do and why. |
| Fix rounds after a review | **2** | Stop. Put the surviving finding in the status block. |
| Questions to Jo at beat 1 | **3** | Decide the rest from the codebase and say which way you decided. |
| Documents written | **0** | The issue, the plan comment and the PR body are the only writings. |

The urge to open a document means the ticket is bigger than this skill. Stop and say so; do not write it.

---

## Beat 1 — PO writes the ticket

In this session. No agent unless the `Is now` column cannot be filled without one.

1. **Resolve the ticket.** `gh issue view <n>`, or write the issue if Jo gave prose. Then move its card, so
   the board shows the work is alive rather than parked in Triage. CI owns `Implement` onward, from the
   branch push and the PR; this one move is yours:

       set -a; source .env; set +a
       PROJECT_OWNER=Past-Lives-Makerspace GITHUB_REPOSITORY=Past-Lives-Makerspace/plfog \
         .venv/bin/python .github/scripts/project_status.py --issue <n> --status Research

   A board that will not answer is one line in the status block, never a reason to stop. `PROJECT_PAT` is a
   session token that dies on re-login, so this failing is routine.
2. **Fill `Is now` by looking**, not by reasoning. Grep for each criterion. **The recon budget is exactly
   this column**: stop searching the moment every row is filled. A row that will not fill from a grep buys
   one scout agent, one page, one question to answer.
3. **Query production when rows are already mid flow** — a state machine, a queryset, a lifecycle, a status
   field. One query, now, before any plan. This is not optional and it is where the only expensive bug of
   the #404 post mortem lived, findable in twenty minutes at hour one.

       DATABASE_URL="$PROD_DATABASE_URL" .venv/bin/python manage.py shell -c "..."

   Print the connected host first. An empty `PROD_DATABASE_URL` silently falls back to local SQLite, and an
   unprovable result is not a result.
4. **Write the out of scope list.** Explicit, in member terms: the things someone could reasonably expect
   from this ticket that it will not do. This is half the scope fence. The other half is the engineer's file
   list at beat 2.
5. **Decide the UX yourself if a screen changes**, naming real components rather than leaving the engineer
   to invent markup. Read `references/ui-checklist.md` and `FRONTEND.md` for this, and only for this: a
   screen is not designed until the ticket names the Add control, the Delete control, the Save action, the
   empty state and the error state.
6. **Ask at most three questions**, each a one line either/or, only where the answer changes the diff.
   `AskUserQuestion`. Anything answerable from the codebase is not a question.

The ticket, on the issue, in this shape:

```
#412 Bounce a submission back · PO · 0 agents · 6 min

  AC   Should be                            Is now
  1    Admin can send a submission back     no control exists
  2    The bounce asks for a reason         n/a
  3    Instructor gets the reason by email  approval email only

  Out of scope  guild lane, bulk bounce, anything but Draft
  Facts         classes/models.py:812 has no returned state
                14 rows sit in review on prod right now
  UX            reuse components/confirm_modal.html, toast on success
  Risky         no. choices only, no money, no auth

  ► needs your go
```

**Gate: Jo approves the ticket.** If he changes it, amend and re-present once. A second rejection means the
ticket is really a conversation: drop the pipeline and have it.

## Beat 2 — the engineer plans

Create the worktree, then spawn the engineer. It writes the plan **before** any code.

    plfog-wt-sweep
    git -C ~/Code/plfog fetch origin
    git -C ~/Code/plfog worktree add ~/Code/plfog-wt/<n> -b <n>-<slug> origin/main
    ln -s ~/Code/plfog/.env  ~/Code/plfog-wt/<n>/.env
    ln -s ~/Code/plfog/.venv ~/Code/plfog-wt/<n>/.venv

Both symlinks are load bearing. Without `.env` the pre push mypy hook dies with a django-stubs INTERNAL
ERROR that looks like a broken plugin and is not, and that error is never a reason to reach for
`--no-verify`.

Spawn **one** `claude` agent and keep its id; you continue the same one for every fix round. Use `claude`,
not the typed `engineer` agent, which is a PHP and Node profile and does not fit Django. Its prompt carries
the ticket, the worktree path, and nothing else of yours.

Its first output is the plan, posted as a comment on the issue, one screen:

```
#412 · Eng plan · 1 agent · 11 min

  Files   classes/{models,views,forms}.py, templates/classes/review.html, classes/spec/
  Change  Status gains RETURNED. Choices only, no DDL.
  Risk    the 14 in review rows must not be stranded
  Won't   guild lane, email template rewrite
```

**Gate, only when the ticket is risky:** post the plan and wait for Jo. Otherwise post it and keep going.

## Beat 3 — the engineer builds

The engineer's prompt carries these facts verbatim, because each one has cost real time here:

- Build to `CLAUDE.md` and `FRONTEND.md`: fat models and skinny views, full annotations including `-> None`,
  `help_text` on every field, `TextChoices`, `dict[key]` over a silent `.get` fallback, no N+1, the
  component library over copied markup.
- Tests are BDD `*_spec.py` under the app's `spec/`, `it_*` inside `describe_*`. **`context_*` is not a
  collected prefix**: a `context_*` block is silently skipped and everything inside it never runs.
- Targeted runs only: `DATABASE_URL=sqlite:///tmp-check.sqlite3 .venv/bin/pytest <paths> -q --no-cov`. The
  global coverage gate fails on every partial run and means nothing there; judge by the pass/fail line.
  `pytest | tail` returns tail's exit code, so never chain a commit onto a piped run.
- `ruff format .` the generated migration too, and run `manage.py check`, which CI runs and local pytest
  skips. Django `E034`, an index name over 30 characters, has broken a build this way.
- Single line `{# #}` template comments only; a wrapped one renders as visible text.
- Commit as `[<area>] <summary>`, no dashes in the message, ending in the session trailer.

Three rules bound the build:

- **The file list is a fence.** An extra file inside an app the plan already named is noted in the next
  status block and the build continues. A file in an app the plan never named **stops the build** and asks
  Jo. Check it with `git diff --name-only origin/main`.
- **The engineer may not add an acceptance criterion.** Things it notices go in a `Noticed, not doing` list
  at the end of the handover, maximum three lines, no recommendation attached.
- **A criterion that turns out unbuildable goes back to the PO**, not around. The engineer stops, says which
  criterion and why; you amend the ticket with Jo or drop the criterion. It never redesigns the product.

Post the status block whenever the engineer checkpoints, so Jo sees movement rather than a silent hour. An
interim checkpoint during a long test run is not the final report; do not act on one.

## Beat 4 — handover

    gh auth switch --user HexagonStorms   # joshplaza is not a collaborator; its writes are rejected

Prefix every `gh` call with `GODEBUG=netdns=cgo`. Bare `gh` fails DNS through Tailscale MagicDNS, and
multi call commands like `gh pr create` fail almost every time without it.

1. **Changelog fragment.** Add exactly one file to `changelog.d/`, named `<pr-number>-<slug>.toml`, per
   `changelog.d/README.md`. **Never touch `plfog/version.py`, `changelog/base.json` or
   `changelog/history.json`**: the version is folded at import, so no PR names a number, nothing collides and
   a rebase cannot leave one stale. CI fails a PR that adds no fragment; a change with genuinely no release
   in it takes the `no-changelog` label, and tooling or test work is `bump` plus `audience = "internal"` and
   nothing else. `release.yml` announces the fragments a push **adds**, so the wording has to be right before
   the merge: refine the one fragment inside the PR rather than adding a second. `CHANGELOG` renders into
   every hub page's context, so a UI copy string in a fragment can trip a negative test assertion; rerun
   `tests/plfog/` after writing it. *(Any guidance saying to bump a `VERSION` literal is stale. Fix it.)*
2. **Push.** The pre push hook runs real ruff and real mypy. A failure there is a real finding: fix, amend,
   push again. Never bypass the hook past red.
3. **Open the PR** against `main` as HexagonStorms. **The PR body is the ticket with `Is now` replaced by
   `Evidence`** — one line per criterion, a spec name for backend, a URL plus a screenshot for a screen. Jo
   reads evidence, not claims. Carry the out of scope list and the `Noticed, not doing` list down as they
   are. Do not also write a prose summary of the table.

   ```
   #412 · handover · 1 agent · 52 min

     AC   Should be                        Evidence
     1 ✅ Admin can send it back           classes/spec/views_spec.py::it_returns
     2 ✅ The bounce asks for a reason     screenshot, review.html
     3 ✅ Instructor gets the reason       classes/spec/emails_spec.py::it_sends_reason

     PR #413 · bot approved · e2e green
     ► yours to merge
   ```
4. **Code review is automatic.** `.github/workflows/bot-review.yml` fires on ready for review, reviews the
   diff as text against `.github/bot-review-prompt.md`, comments blockers and approves a clean diff. It never
   checks the code out, so it cannot run anything and has never seen the ticket. Do not spawn an agent to
   duplicate it. Re-review after a fix is the `bot-review` label; the manual path is `/pl-bot-review-pr`.
5. **Second reviewer, only if the ticket is risky or the diff is over 800 lines.** A fresh `claude` agent,
   reviewing the diff **against the acceptance criteria**, which is the thing the bot cannot do. It reads
   with `gh pr diff <n>`. **Never `gh pr checkout`**: that moves the shared working tree and strands the
   engineer's fixes. It never edits a file and never posts to GitHub; it returns findings as `file:line`,
   each labelled with the criterion it breaks, or `no findings`. Findings go to the engineer by
   `SendMessage`, which has the context; then the same reviewer verifies the delta against the pushed commit,
   because a fix that introduces a new variant of the same bug has happened here. Two rounds, then stop.
6. **The merge gate is targeted tests, bot approval, lint and e2e.** The `test` job is the hour long mutation
   run and is not a blocker; do not wait for it, and never cite a green run as a mutation result, because
   pytest-leela no-ops when a run exits non-zero and the gate can be silently off.
7. **Stop.** **Agents do not merge.** A merge to main deploys to Render and announces to members; that is
   Jo's.

## Resuming

There is no state file. `/drive 412` on a ticket in flight reads the issue, the plan comment, the branch and
the PR, rebuilds the status block from those, and continues. If those do not say where the ticket is, the
fix is to write the missing fact onto the issue, not to start keeping a checkpoint.

## Stop rather than grind

None of these is a failure. Grinding past one is. Stop, post the status block, say the one sentence.

- A third agent, a third fix round, or a document is what comes next.
- The build needs a file in an app the plan never named.
- A criterion turns out unbuildable, or a review finding invalidates the plan rather than the diff.
- The ticket is really several tickets. Say which, and stop.
- Jo is present and about to get a long silent stretch. Post the status block first.
