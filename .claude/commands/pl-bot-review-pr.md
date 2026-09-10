Review the current PR and, if it passes review, approve it as PastLivesReviewBot.

This is the hands-on version of the review. The `Bot review` workflow
(`.github/workflows/bot-review.yml`) does the same job unattended on every PR
that is marked ready for review — this command is for when you are at the
keyboard and want the review now, deeper, and arguable. You have the repo
checked out, so you can run the tests, open the files the diff only grazes, and
follow a call site. The workflow sees the diff and the standards docs, nothing
more.

Running this on a PR the workflow already approved is harmless: `main`'s ruleset
needs one approving review, and a second is simply recorded.

## Steps

1. Find the open PR for the current branch:
   ```
   gh pr view --json number,title,url,body,headRefName,baseRefName
   ```

2. Get the full diff against the base branch:
   ```
   gh pr diff
   ```

3. **Review it against `.github/bot-review-prompt.md`.** That file is the rubric
   — the same one the workflow uses — and it is the single source of truth for
   what counts as a blocker here. Read it and follow it. Do not review from
   memory of the standards; the rubric exists so the two reviewers cannot drift
   apart.

   Being local, you can go further than the workflow can. Where the diff makes
   you suspicious, open the surrounding code, check whether a spec actually
   covers the new branch, and run the relevant tests.

4. If there are blockers, post a **comment** (not an approval) as
   PastLivesReviewBot, and tell the user what needs fixing:
   ```
   BOT_PAT=$(grep '^BOT_PAT=' .env | cut -d= -f2) && \
   GH_TOKEN=$BOT_PAT gh pr comment <number> --body "<findings, grouped by file:line>"
   ```

5. If it passes, post a formal **APPROVE** review as PastLivesReviewBot:
   ```
   read OWNER REPO < <(gh repo view --json owner,name -q '.owner.login + " " + .name')
   BOT_PAT=$(grep '^BOT_PAT=' .env | cut -d= -f2) && \
   GH_TOKEN=$BOT_PAT gh api --method POST "repos/$OWNER/$REPO/pulls/<number>/reviews" \
     -f event='APPROVE' \
     -f body='Reviewed and approved by PastLivesReviewBot. Code meets PLFOG coding standards.'
   ```
   Then confirm the approval to the user.

## Important

- `BOT_PAT` must come from `.env` in the same command that uses it; env vars do
  not persist between tool calls. Never let a bot review fall back to ambient
  `gh` auth — that posts as the wrong account.
- Never approve without actually reading the diff.
- Be strict. The rubric is the contract, including its list of things that are
  explicitly *not* blockers.
