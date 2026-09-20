# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues on `Past-Lives-Makerspace/plfog`, and issues are cards on the kanban board (GitHub project 1 of the org). Use the `gh` CLI for all operations.

## House rules

- Prefix every `gh` call with `GODEBUG=netdns=cgo`; bare `gh` fails DNS through Tailscale MagicDNS on this machine.
- `HexagonStorms` must be the active `gh` account (`gh auth status`); other accounts are not collaborators and their writes are rejected.
- An issue's body follows [CONTRIBUTING.md](../../CONTRIBUTING.md) and the Ticket form (`.github/ISSUE_TEMPLATE/ticket.yml`): the form's labels as `### ` headings, in the form's order. Blank issues are disabled, and `gh issue create` bypasses the form, so the shape is the writer's job.
- Every new issue is typed (Bug, Feature, Task) and labelled (`bug`, `enhancement`, `documentation`, or none), and carries Priority, Size and Estimate on the board. `/create-issue` (`.claude/skills/create-issue/SKILL.md`) does all of this from prose and is the way to file one; a hand-run `gh issue create` reproduces its §5 and §6.
- Where an issue stands in the work is the board's Status column (Backlog, Todo, Research / Finding Facts, Plan / Choosing Approach, Implement / Building, Present / In Review, Observe / Checking Production, Done), read visually. Labels never stand in for it.

## Conventions

- **Create an issue**: `/create-issue <prose>`. By hand: `gh issue create --title "..." --body-file <file> --label enhancement --type Feature`, then `gh project item-add 1 --owner Past-Lives-Makerspace --url <url>` and one `gh project item-edit` call per board field.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v`; `gh` does this automatically when run inside a clone.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either: resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

File a GitHub issue through `/create-issue`, so it lands in the Ticket shape with a type, a label and its board fields. Hand it the skill's draft as the prose; `/create-issue` fills current behavior by reading the code, never from the draft alone.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies**, the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only, the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me`, the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.
