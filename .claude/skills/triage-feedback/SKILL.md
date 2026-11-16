---
name: triage-feedback
description: >-
  Work through the open requests in the plfog Feedback Inbox: read each from production, diagnose bug
  reports with /diagnosing-bugs, and decide whether it needs a ticket and a build now, a ticket only, a
  quick production change with no ticket, or just a reply. Draft a reply of at most 160 characters in
  Felix's voice, and get Felix's yes on the route and the reply before filing, changing or sending
  anything. Use when the user types /triage-feedback, or asks to go through, check or triage open,
  unsolved or new feedback.
---

# /triage-feedback: every open request gets a route and a reply

    /triage-feedback          every open request
    /triage-feedback 14 16    just these

Nothing is filed, changed in production or sent to a member before Felix approves it in step 4. Steps 1
to 3 only read.

## 1. Read what is open

Read production with `zsh ~/Code/brain/bin/prod-read -C <repo> '<snippet>'` (read only at the server;
it prints the host first). Use `FeedbackRequest.objects.open().with_thread()` from `core.models`, or the
requests named. That covers every request not closed, plus closed ones the sender has replied to since.

For each, collect `pk`, `category`, `status`, `subject`, `message`, `github_issue_url`, `created_at`, the
sender's first name, every comment (author, date, body, photo count) and the request's own photo count.

Skip one whose last word is already Felix's and whose status is past Received: it is waiting on work,
not on triage. List it in the report as "waiting on #N" and move on. A sender reply since
(`member_replied_at` after `staff_seen_at`) always counts as new.

## 2. Find out what is true

For each request, before forming a view:

- **Already in flight?** Search issues and PRs for it (`GODEBUG=netdns=cgo gh issue list --search`,
  `gh pr list --state all --search`), fragments that list it (`grep -rn "requests = \[.*<pk>" changelog.d/`),
  and the `-wt` worktrees. A match changes the route to "point at it", never a duplicate ticket.
- **A bug report:** run `/mattpocock-skills:diagnosing-bugs` on it against the local stack, never
  production. The outcome is one line: confirmed with the cause, not reproducible, or works as designed.
  With more than one bug report, give each its own subagent running that skill. A subagent changes no
  code and returns that one line plus the evidence.
- **A feature request:** read the code and the page. Does it already exist under another name? How big is
  it, roughly (size S, M or L in `/create-issue` terms)?
- **General feedback:** it has no Planned or Live status. It is answered with a comment and maybe closed.

## 3. Pick a route

| Route | When | What happens on a yes |
|---|---|---|
| **Build now** | A confirmed bug or a wanted feature, small enough to start today | `/create-issue`, then `/drive` it in its own agent; status Building once the PR opens |
| **File only** | Real and wanted, but not now, or too big for today | `/create-issue` to Backlog; status Planned; issue link on the request |
| **Prod change** | Fixed by a data or settings change in production: a role, a permission, a typo in a record, a Site Settings switch. No code. | Make the change through the app's own admin as Felix (never raw SQL), then read it back |
| **Reply only** | Already exists, works as designed, already in flight, needs more from the sender, or general feedback | Send the reply; status per the verdict |

Not a bug and Not planned are Felix's calls. Recommend them, never decide them.

## 4. Ask Felix

One `AskUserQuestion` question per request, four per call at most. Each opens with two plain sentences,
"Issue: ..." (what goes wrong for the member, or what they want) and "Solution: ..." (the recommended
route and why), and nothing more technical than that. The options are the routes that fit, recommended
first and marked, each with its consequence in its description: what gets filed, what changes in
production, which status the member sees.

Put the drafted reply in each option's `preview`, exactly as it would be sent, with its character count
under it. The reply follows [`.claude/skills/felix-persona.md`](../felix-persona.md): read it before writing
any. Hard rules: 160 characters at most, the sender's first name, buttons and tabs named exactly as the
page labels them, and every clause true. If the route is Build now or File only, the reply does not
promise a date.

Felix may answer with "Other" to change the route or rewrite the reply. His wording is the one sent.

## 5. Do what he approved, in order

1. **The work.** Ticket via `/create-issue` (it handles the board). Build via `/drive` in a fresh agent,
   one per ticket. A prod change through the app as Felix, read back after.
2. **The reply and status**, signed in as Felix, at
   `https://members.pastlives.space/manage/feedback/<pk>/` in a new Chrome tab (never the marketing host):
   - **Not planned or Not a bug:** the reply goes in **Where It Stands**' **Comment** field with the
     status, then **Save**. The form refuses those statuses without it, and the member gets one message.
   - **Everything else:** type the reply in **Comment to <name>** and press **Send to <name>** first.
     Then set **Where It Stands** and **GitHub issue link** and **Save**, leaving its Comment empty.
   - Never **Mark Live** or **Mark Fixed**: it carries no comment.
3. **Proof.** Read production again: the newest comment is the approved text, the status and issue link
   are as approved.

A request whose route Felix declined or left unanswered gets nothing sent.

## 6. Report

One line per request: `#<pk> <route>: <what was done>`, with the issue or PR link. Then the skipped
"waiting on" list, if any.
