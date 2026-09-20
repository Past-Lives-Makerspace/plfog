---
name: create-issue
description: >-
  Write and file one plfog GitHub issue in the shape CONTRIBUTING.md and the Ticket form require, from a
  sentence or a paragraph of prose: user story, summary, goal, current and expected behavior found by
  reading the code, observable acceptance criteria, area, constraints and out of scope. Then type and
  label it and put it on the kanban board with a priority, a size and a story point estimate. Use when
  the user types /create-issue, says file, open, make or write an issue or a ticket, asks to put
  something on the board, or describes a bug or a feature they want tracked rather than built now. Not
  for PR descriptions, and not for building the thing (that is /drive).
---

# /create-issue — one ticket, written the way the board reads it

    /create-issue members can't tell which guild a class belongs to on the catalog card
    /create-issue preview <the same prose>      # write and show the ticket, file nothing
    /create-issue --parent 358 <prose>          # a sub-issue of an epic; --blocked-by 412 also passes through

Issues here are read by council members who are not engineers and by agents that build straight from
them (`/drive`), so the ticket is the spec: a thin one becomes a wrong build. The time goes into looking
at the code, and the writing is the record of what was found.

## 1. Read the contract, then the exemplars

- `CONTRIBUTING.md` is the shape and `.github/ISSUE_TEMPLATE/ticket.yml` is the field list, in order.
  `gh issue create` bypasses the form, so the body is the form's labels as `### ` headings in the form's
  order, with `### Notes` allowed after them.
- Two house exemplars, a feature and a bug: `gh issue view 465` and `gh issue view 466`. Match their
  register: plain sentences a non-engineer follows, code named by path and line wherever a line settles a
  fact, and nothing decorative.

## 2. Look before writing

Current behavior is filled by reading, never by reasoning from the prose.

- Search for an existing ticket first: `gh issue list --state all --search "<two key words>"`. One that
  already covers the ask ends the run with its number; one that overlaps is named under Notes.
- Find the models, views, forms and templates the prose touches. `CODEBASE_INDEX.md` maps the apps and
  an app's `AGENTS.md`, where it has one, carries its detail. Name them by path and line; the numbers are
  what make the ticket buildable without a second search. The shell is zsh, so quote globs in a grep
  (`--include='*.py'`); unquoted, the sweep returns nothing and says so quietly.
- The prose is a report, not a finding. Check who can actually do the thing it describes and on which
  screen; a ticket written against the wrong actor builds the wrong hook.
- Read the parts of `STANDARDS.md` §5 Permissions and §11 Domain Traps that touch the area (sequential
  class review, two push channels, demo content gates, the access decorators). A trap the ticket names in
  Constraints is one the builder does not fall into.
- A ticket that changes what happens to rows already sitting in a state (a queue, a status machine, a
  lifecycle a change could strand) gets one read-only count from production in Current behavior, because
  that is where the expensive surprises live:

      set -a; source .env; set +a
      DATABASE_URL="$PROD_DATABASE_URL" .venv/bin/python manage.py shell -c "..."

  Print the connected host first; an empty `PROD_DATABASE_URL` falls back to local SQLite without a
  word. One attempt. When the command is refused or the variable is unset, the ticket names the count
  worth taking under Notes and the run moves on.
- When the code already does what the prose asks, say where and stop. That is a finding, not a ticket.

## 3. Ask at most three questions

Only where the answer changes the ticket and the code cannot answer it: who controls a feature (site
admin versus guild lead versus equipment manager), who pays, where a setting lives, anything that emails
or posts to every member. Each question is one line with a recommended default, through `AskUserQuestion`.
A ticket that leaves a policy question open is a guess dressed as a spec, and #408 was built exactly that
way and closed unmerged.

Everything else is decided from the codebase, and the ticket says which way. Product choices (which
roles, which cadences, which surfaces) are decided in the ticket, never offered as a menu, because a
builder given a menu picks one and the ask was all of them. Implementation choices go under Notes with a
recommendation.

In `preview`, or when nobody is there to answer, take the recommended default for each question and
list the question with the default under `Decided` in the report, so Felix can overrule it before the
ticket is filed.

## 4. Write the body

| Section | What it holds |
|---|---|
| User story | When a person is involved: "As a [member / guild lead / instructor / admin], when [situation], I would like [X] to do [Y]. Currently, [X] does [Z]." Tooling and refactors skip it. |
| Summary | One line, at most 160 characters, for someone who has never seen the code. It also seeds the title. |
| Goal | Why it matters: who is blocked or hurt today, in a sentence or two. |
| Current behavior | What the code does, by path and line, and the manual workaround if one exists. |
| Expected behavior | The product in member terms. When a screen changes, name the real screen, tab and component (`FRONTEND.md`), so the builder is not inventing markup. |
| Acceptance criteria | A checklist. Each item observable and testable by someone who did not write the code. Behavior ends with a "Specs cover: …" item; a screen change ends with "The PR shows a screenshot of …". |
| Area | The features and kinds of files involved, not a file list. |
| Constraints | Limits from the standards and the domain: reuse this form, no migration, admin only through this decorator, silent (no email, no Discord). |
| Out of scope | What someone could reasonably expect that this will not do, in member terms, with follow-ups named as follow-ups. |
| Notes (optional) | Implementation options with a recommendation, a Relevant files table when recon found one, superseded or related issues. |

A criterion is the builder's target and the reviewer's rubric, so it names the surface and the outcome:

    weak    Orientations are visible on the member page.
    strong  On Manage Members > edit member, an Orientations tab lists each completed orientation with
            type, owner, date and who ran it.

    weak    Recording does not notify anyone.
    strong  Recording sends no email and emits no Discord event; specs assert the outbox and the emitter
            stay empty.

The body is as long as the criteria need and no longer. A big ask is fine: the ticket says how it ships
as parts ("part 1 of 3"), each under about 400 changed lines.

## 5. Classify and size

**Title.** The summary's shorter cousin, one plain sentence, prefixed with the owning app in brackets
when one app owns it (`[hub]`, `[classes]`, `[billing]`, `[core]`, `[membership]`; `[ops]` for tooling,
`[frontend]` for CSS-wide work) and bare when it spans apps. The squash commit of the PR takes its own
title, so this one is for the board.

**Type and label.** Both, because the board and the existing issues show labels and GitHub's own filters
use the type.

| Type | Label | When |
|---|---|---|
| Bug | `bug` | Production does the wrong thing today. |
| Feature | `enhancement` | Something members or admins cannot do yet. |
| Task | none, or `documentation` for docs | Tooling, chores, discovery, docs. |

Add `nice to have` when Felix says it is optional or someday.

**Priority.** A proposal, stated with its reason in the report, because it is a one-click edit on the
board and Felix's call:

- **P0** members are hurt now (money, data, a broken flow), or a dated launch depends on it.
- **P1** a real need with a workaround; what comes after P0.
- **P2** polish, tidy-ups, discovery.

**Size and Estimate.** Size is the T-shirt, Estimate is the story points, and the two move together.
The scale is calibrated on cards already on the board, so a new card lands beside its peers:

| Size | Points | Looks like | Peers |
|---|---|---|---|
| XS | 1 | Copy, or one template line; specs untouched or one assertion. | #372 rename a button |
| S | 2 | One view, form or model method plus its specs, in one app. | #400, #424 |
| M | 5 | One feature across a model, a view, a template and specs in one app, or a new email or notification on an event that already exists; a choices-only migration at most. | |
| L | 8 | Spans apps with a new screen or a new event and its email pair, or carries real DDL. | #399, #432 |
| XL | 13 | Several PRs' worth. The ticket says how it splits. | |

Size the diff, not the importance. Then go one size up when the work touches money, auth and
permissions, member-wide email or Discord, or real DDL (a column, table or index; a choices-only
`AlterField` is not DDL). Those are the four things that have cost real time here, and a ticket carrying
one is never small however short the diff.

## 6. File it

`HexagonStorms` must be the active `gh` account (`gh auth status`); `joshplaza` is not a collaborator
and its writes are rejected. Every `gh` call is prefixed `GODEBUG=netdns=cgo`, because bare `gh` fails
DNS through Tailscale MagicDNS on this machine.

    gh issue create --title "<title>" --body-file <scratch>/issue.md --label enhancement --type Feature
    gh project item-add  1 --owner Past-Lives-Makerspace --url <issue url>
    gh project item-edit 1 --owner Past-Lives-Makerspace --url <issue url> --field Priority --value P1
    gh project item-edit 1 --owner Past-Lives-Makerspace --url <issue url> --field Size     --value S
    gh project item-edit 1 --owner Past-Lives-Makerspace --url <issue url> --field Estimate --number 2

`item-edit` takes one field per call. The board auto-adds a new issue to Backlog within a minute;
`item-add` is idempotent and makes it immediate, so the edits never race the auto-add. Status stays
Backlog unless Felix said "todo" or "next", then `--field Status --value Todo`.

`preview` writes the body to the scratchpad, prints it in the reply followed by the report block from
§7, and stops. Nothing reaches GitHub.

## 7. Report

One block, so Felix can see every judgment call at a glance and fix a wrong one on the board:

    #468 [hub] Members see which guild a class belongs to on the catalog card
      https://github.com/Past-Lives-Makerspace/plfog/issues/468
      Feature · enhancement · P1 · S · 2 points
      Why P1     members ask in Discord; the class page already shows it, so there is a workaround
      Why S      one template and its context, one spec; no model change
      Decided    the guild name, not the category name: Category is labelled Guild in the UI
      Board      Backlog

## Stop rather than guess

- An existing issue already covers it: say which, and stop.
- The code already does it: say where, and stop.
- The ask is several tickets: say how it splits and file after Felix picks, unless the split is one
  epic with named parts, which is one ticket.
- A policy question got no answer: `preview`, never a filed guess.
