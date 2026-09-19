# Contributing to plfog

Issues and pull requests are read by maintainers, Past Lives council members and AI agents, several of them not engineers. Both follow the rules below. The issue form and the PR template lay them out, and CI enforces the PR rules. How code itself is written is in [STANDARDS.md](STANDARDS.md).

## Both issues and PRs

1. **User story first, when a person is involved:** "As a [member / guild lead / instructor / admin], when [situation], I would like [X] to do [Y]. Currently, [X] does [Z]." Skip it for tooling and refactors.
2. **Summary:** one line of at most 160 characters that someone who has never seen the code understands.
3. **Current and expected behavior**, stated plainly.
4. **Area:** the features and kinds of files involved, not a file list. For example: "notification settings: models, the settings page and its emails".

## Issues

Every card on the project board is an issue written with the **Ticket** form, which also asks for:

- **Goal:** why this matters, in a sentence or two.
- **Acceptance criteria:** a checklist; each item observable and testable.
- **Constraints:** limits the solution must respect (no migration, reuse the existing modal, must not email members), when known.
- **Out of scope:** what someone could reasonably expect that this will not do.

A big issue is fine. Ship it as several PRs, each saying which part it is ("#123, part 1 of 3").

## Pull requests

Use the template. Its headings are checked exactly:

```markdown
As a ..., when ..., I would like ... Currently, ...   (optional)
**Summary:** up to 160 characters, for a non-technical reader.
**Area:** notification settings: models, the settings page and its emails.

### Problem
One sentence: what happens now and what should happen, or `Closes #123`.

### Solution
- Two to four bullets with the key changes.

### Impact / Risks
One line: migrations, breaking changes, or "None".

### Verification
The exact command or check that proved it.
```

- **At most 300 words.** Less is more. Every file is already in the diff; the description says what and why.
- **Aim for 400 changed lines of code or fewer.** Docs (`.md`), migrations, lock files, images and changelog fragments do not count. Split bigger work into parts of one issue.
- **Changes members can see need pictures.** A PR that touches `templates/`, `static/css/` or `static/js/` adds at least one screenshot or mockup under [`mockups/screenshots/`](mockups/screenshots/README.md) and shows it in the description.
- Every PR also adds a changelog fragment (`changelog.d/README.md`).

## What CI checks

`.github/workflows/pr-description.yml` fails a PR whose description has no summary or one over 160 characters, has no Area line, is missing a section, has a Solution outside 2 to 4 bullets, runs past 300 words, or changes templates, CSS or front-end JS without adding an image under `mockups/`. Above 400 changed lines it warns without failing. When a template change shows nothing (a comment, a refactor), add the `no-screenshots` label. PRs opened by bots are skipped. Editing the description re-runs the check.
