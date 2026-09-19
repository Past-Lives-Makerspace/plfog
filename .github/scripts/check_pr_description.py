"""Fail a pull request whose description breaks the house format in CONTRIBUTING.md.

The review bot reads the diff and never the description, so nothing else holds a PR to the
format a non-technical reader relies on: a summary of at most 160 characters, the four-part
Problem / Solution / Impact / Verification skeleton, and at most 300 words. It also fails a PR
that changes what members see (templates, CSS, front-end JS) without adding a screenshot or
mockup under ``mockups/``, and warns, without failing, when more than 400 lines of code change.

Stdlib only and no Django, like ``check_changelog_fragment.py``: it runs in its own workflow so
an edited description re-checks in seconds.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

MAX_SUMMARY_CHARS = 160
MAX_WORDS = 300
TARGET_LINES = 400
SECTIONS = ("Problem", "Solution", "Impact / Risks", "Verification")
SKIP_PICTURES_LABEL = "no-screenshots"

_UI_PREFIXES = ("templates/", "static/css/", "static/js/")
_PICTURE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
#: Changed lines here are generated or not code (nor is any ``.md``), so they do not count toward the size target.
_UNCOUNTED_PREFIXES = ("changelog.d/", "changelog/", "mockups/", "static/help/")
_UNCOUNTED_NAMES = ("uv.lock", "package-lock.json", "poetry.lock")

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_WORD = re.compile(r"[A-Za-z0-9][\w'’.-]*")
_SUMMARY = re.compile(r"^\*\*Summary:\*\*[ \t]*(.*)$", re.MULTILINE)
_AREA = re.compile(r"^\*\*Area:\*\*[ \t]*\S", re.MULTILINE)
_HEADING = re.compile(r"^#{2,3}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_BULLET = re.compile(r"^(?:[-*]|\d+\.)[ \t]+\S", re.MULTILINE)


def _sections(body: str) -> dict[str, str]:
    """Map each ``##`` or ``###`` heading to the text beneath it, headings normalised."""
    matches = list(_HEADING.finditer(body))
    found: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        name = re.sub(r"\s*/\s*", " / ", match.group(1))
        found[name] = body[match.end() : end].strip()
    return found


def word_count(body: str) -> int:
    """Words a reader reads: HTML comments and link or image targets do not count."""
    visible = _LINK_TARGET.sub("]", _COMMENT.sub("", body))
    return len(_WORD.findall(visible))


def description_errors(body: str) -> list[str]:
    """Every way ``body`` breaks the pull request format, in reading order."""
    text = _COMMENT.sub("", body)
    errors: list[str] = []

    summary = _SUMMARY.search(text)
    if summary is None or not summary.group(1).strip():
        errors.append("Missing the `**Summary:**` line: one sentence a non-technical reader understands.")
    elif len(summary.group(1).strip()) > MAX_SUMMARY_CHARS:
        errors.append(f"The summary is {len(summary.group(1).strip())} characters; keep it to {MAX_SUMMARY_CHARS}.")

    if _AREA.search(text) is None:
        errors.append("Missing the `**Area:**` line: the features and kinds of files this touches.")

    sections = _sections(text)
    for name in SECTIONS:
        if not sections.get(name):
            errors.append(f"Missing or empty `### {name}` section.")

    bullets = len(_BULLET.findall(sections.get("Solution", "")))
    if sections.get("Solution") and not 2 <= bullets <= 4:
        errors.append(f"`### Solution` has {bullets} bullet(s); use 2 to 4.")

    words = word_count(body)
    if words > MAX_WORDS:
        errors.append(f"The description is {words} words; keep it to {MAX_WORDS}. Less is more.")

    return errors


def needs_pictures(changed: list[str]) -> bool:
    """True when the change touches something members see."""
    return any(path.startswith(_UI_PREFIXES) for path in changed)


def adds_pictures(added: list[str]) -> bool:
    """True when the change adds a screenshot or mockup under ``mockups/``."""
    return any(path.startswith("mockups/") and path.lower().endswith(_PICTURE_SUFFIXES) for path in added)


def counted_lines(numstat: str) -> int:
    """Changed lines of code from ``git diff --numstat``, skipping binaries and generated files."""
    total = 0
    for line in numstat.splitlines():
        added, deleted, path = line.split("\t", 2)
        if added == "-" or "/migrations/" in f"/{path}" or path.startswith(_UNCOUNTED_PREFIXES):
            continue
        if path.rsplit("/", 1)[-1] in _UNCOUNTED_NAMES or path.endswith(".md"):
            continue
        total += int(added) + int(deleted)
    return total


def _git(*args: str) -> str:
    completed = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if completed.returncode:
        sys.exit(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def main() -> None:
    body = os.environ.get("PR_BODY") or ""
    labels = {str(label).strip().lower() for label in json.loads(os.environ.get("PR_LABELS") or "[]")}
    span = f"{os.environ['BASE_SHA']}...{os.environ['HEAD_SHA']}"

    errors = description_errors(body)

    changed = _git("diff", "--name-only", span).split()
    added = _git("diff", "--name-only", "--diff-filter=A", span).split()
    if needs_pictures(changed) and not adds_pictures(added) and SKIP_PICTURES_LABEL not in labels:
        errors.append(
            "This PR changes templates, CSS or front-end JS but adds no screenshot or mockup under "
            f"`mockups/screenshots/`. Add one and show it in the description, or add the "
            f"`{SKIP_PICTURES_LABEL}` label if nothing visible changes."
        )

    lines = counted_lines(_git("diff", "--numstat", span))
    if lines > TARGET_LINES:
        print(
            f"::warning::{lines} changed lines of code; aim for {TARGET_LINES} or fewer. "
            "Split big work into parts of one issue."
        )

    if errors:
        sys.exit("The PR description does not follow CONTRIBUTING.md:\n\n- " + "\n- ".join(errors))
    print(f"PR description OK: {word_count(body)} words, {lines} changed lines of code.")


if __name__ == "__main__":
    main()
