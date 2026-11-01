---
name: respond-to-feedback
description: >-
  Answer one member's feedback request in the Feedback Inbox the way Felix does: one sentence that says
  where the request stands (live, building, planned, not a bug, not planned, closed) and, when it is live,
  where to go to use it, plus at least one photo when something visible shipped. Sets the matching status
  and checks in production that the reply landed. Use when the user types /respond-to-feedback, names a
  feedback request number ("reply to #7"), or asks to tell a member their request is done.
---

# /respond-to-feedback: one sentence and a photo

    /respond-to-feedback 7
    /respond-to-feedback 7 9 12

A member who asked for something wants to know one thing: what happened to it. The reply says that in one
sentence, and when there is something new to look at, shows it. Nothing else.

## 1. Read the request and its trail

Read production, read only. Print the connected host first; an empty `PROD_DATABASE_URL` falls back to
local SQLite and proves nothing.

    DATABASE_URL="$PROD_DATABASE_URL" .venv/bin/python manage.py shell -c "..."

From `core.models.FeedbackRequest` (`pk`): `subject`, `message`, `status`, `github_issue_url`,
`status_changed_at`, the sender's first name, and every `comments` row (with its `photos` count). A request
already answered with the same news gets nothing new: say so and stop.

Then find the work: the issue in `github_issue_url`, or a fragment that lists the request
(`grep -rn "requests = \[.*<pk>" changelog.d/`, and `git log -S "requests = [" -- changelog.d/` for spent
ones), and its PR. Prefix every `gh` call with `GODEBUG=netdns=cgo`.

## 2. Decide the verdict from evidence

| Verdict | Only when | Status |
|---|---|---|
| Live | the PR is merged **and** production serves it: its migration is applied, or the version badge on `/accounts/login/` moved past the merge. A merged PR that has not deployed is not live. | Live |
| Building | an open PR or a branch exists for it | Building |
| Planned | an issue exists, nothing built | Planned |
| Already there | it works today with no change: point to it | Live |
| Not a bug | it behaves as designed: say how it works | Not a bug |
| Not planned | Felix decided against it | Not planned |
| Closed | resolved outside the app, or a duplicate | Closed |

Not planned and Not a bug are Felix's calls. Never pick them on your own reading; ask him with
`AskUserQuestion` first. Every other verdict follows from the evidence.

## 3. Write one sentence

`Hi <first name>! <verdict>: <where to go and what to press>.` One sentence after the greeting, in
Felix's voice:

- **Live:** the path by its real labels, the way the page says them. "This is live now: go to Teaching,
  then My Classes, and press Email Students to email everyone who took your classes."
- **Building or Planned:** what is coming in the member's words, never a date.
- **Not a bug or Already there:** how it works today and where. **Not planned or Closed:** the reason,
  kindly.

Rules: no dashes of any kind; name buttons and tabs exactly as labelled (read the template or the merged
guidance steps in `core/guidance.py`); no feature the member did not ask about; no promises. A third
person in the sentence is "they" unless their pronouns are on record.

## 4. Pick the photo

**Live and something visible changed: at least one photo is required.** Otherwise no photo.

1. **The PR's own screenshots first.** `gh pr view <n> --json body` lists R2 URLs. Download the one
   showing where to go (the button, the tab), plus the result if it helps. Two at most.
2. **None fit?** Take one with a throwaway Playwright spec under `tests/e2e/` that seeds sample data and
   saves the page (pattern: `tests/e2e/screenshots_spec.py`, `login_via_code`). Delete the spec after;
   never commit it.
3. **Never a production screenshot.** It shows other members' names and data.

Look at every image before attaching it: no half open drawers, no other member's details. Copy the files
into the session scratchpad; the browser upload only reads from there.

## 5. Send it as Felix

The Feedback Inbox at `https://members.pastlives.space/manage/feedback/<pk>/` (never the marketing
host), in Chrome, signed in as Felix. Open a new tab rather than reusing his.

1. Type the sentence in **Comment to <name>**, attach the photos to **Photos (optional)** with the file
   upload tool on that input, and press **Send to <name>**.
2. Then set the status under **Where it stands** and press **Save**, if it changed.

The **Mark Live** and **Close** buttons under Resolve it do **not** carry the comment box's text, so a
reply sent that way arrives empty. Send the comment first, always.

Deploying a fragment with `requests = [<pk>]` already marks the request Live and sends the member an
automatic notice. The reply adds the how; it is not a duplicate.

## 6. Prove it landed

Read production again: the newest comment's body starts with your sentence, its `photos` count is what
you attached, and `status` is the verdict. Report to Felix in two lines: the sentence as sent, and the
photo count and status. A page that says "Sending..." is not proof.
