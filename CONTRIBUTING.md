# Contributing to plfog

Issues and pull requests are read by maintainers, Past Lives council members and AI agents, several of them not engineers, so both follow one plain shape. How code is written is in [STANDARDS.md](STANDARDS.md).

## Both issues and PRs

1. **User story first, when a person is involved:** "As a [member / guild lead / instructor / admin], when [situation], I would like [X] to do [Y]. Currently, [X] does [Z]." Tooling and refactors skip it.
2. **Summary:** one line of at most 160 characters that someone who has never seen the code understands.
3. **Current and expected behavior**, stated plainly.
4. **Area:** the features and kinds of files involved, not a file list. For example: "notification settings: models, the settings page and its emails".

## Issues

Every card on the project board is an issue written with the **Ticket** form (`.github/ISSUE_TEMPLATE/ticket.yml`). Besides the four parts above it asks for a **goal** (why this matters), **acceptance criteria** (a checklist, each item observable and testable), **constraints** the solution must respect, when known, and what is **out of scope**.

A big issue is fine: ship it as several PRs, each naming its part ("#123, part 1 of 3"). Only the last part closes the issue ("Closes #123, part 3 of 3"); GitHub closes an issue on any merged PR that says `Closes`, so an earlier part must not.

## Pull requests

Start from `.github/pull_request_template.md`: the Summary and Area lines, then `### Problem` (one sentence, or `Closes #123`), `### Solution` (2 to 4 bullets), `### Impact / Risks` (one line) and `### Verification` (the exact command or check that proved it). The diff already lists every file; the description says what and why.

Rules marked ✓ are checked by `.github/workflows/pr-description.yml`, which re-runs when you edit the description:

- ✓ The summary is present and at most 160 characters; the Area line and all four sections are present; Solution has 2 to 4 bullets.
- ✓ The `**Guidance:**` line says how much teaching the change ships, `none`, `link`, `shots` or `tour`, and matches the `guidance` of the fragment the PR adds (`docs/GUIDANCE.md`).
- ✓ At most 300 words.
- ✓ A PR naming its part in `### Problem` ("part 3 of 3") says `Closes #N` there when it is the last part, and does not when it is an earlier one.
- ✓ A PR touching `templates/`, `static/css/` or `static/js/` shows a screenshot or mockup in the description. Images are never committed: upload each with `.venv/bin/python scripts/upload_screenshot.py <file>` and paste the Markdown it prints, or, without the R2 keys, drag it into the description ([`mockups/screenshots/`](mockups/screenshots/README.md)). The check fails a committed image and a relative link. Uploaded screenshots are deleted after 30 days. When such a change shows nothing (a comment, a refactor), add the `no-screenshots` label.
- ✓ (warning) About 400 changed lines of code or fewer. Docs, migrations, lock files, images and changelog fragments do not count.
- Every PR also adds a changelog fragment (`changelog.d/README.md`).
- No PR adds or extends a Help Center article (#1009). Teaching ships as a What's New entry, captioned screenshots it walks through or a tour, at the level the Guidance line declares; a diff that adds a button, field, tab or flow to a template ships at `shots`.

Bots' PRs are exempt. A maintainer can exempt any other PR with the `no-description-check` label, for one opened before these rules or one that cannot follow them.
