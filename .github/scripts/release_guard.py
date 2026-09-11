"""Fail the release run when a push ships a release that announces nothing.

A push to ``main`` that edits ``plfog/version.py`` but leaves ``VERSION`` exactly where it
was deploys to production and tells nobody: ``discord-notify.yml`` used to set
``should_post=false`` and exit zero, so the Actions tab stayed green and the missing
announcement was invisible until somebody noticed. That happened on the merge that shipped
the lobby slideshow (``11a1693d``, v1.49.0, #348), and once before it on the hand-written
repair of the same bug (``920fab64``, v0.23.39). Replayed over every push to ``main`` that
touched ``plfog/version.py``, those two are the only ones this guard would have failed —
2 of 222, no false positives.

So the skip is now a non-zero exit. **A red X on the Actions tab is the whole alert**: no
Discord ping, no auto-filed issue, no notification of any kind outside GitHub. That is a
deliberate decision, not an omission.

The comparison base is ``github.event.before`` — the tip of ``main`` before the push — not
``HEAD^``. A rebase merge pushes every commit of the PR at once, and ``HEAD^`` is then a
commit from inside that PR, which may already carry the bump. ``discord-notify.yml``
checks out with ``fetch-depth: 0`` so that base is always reachable.

Run by ``.github/workflows/discord-notify.yml``. Stdlib only: the release workflows install
nothing beyond the interpreter.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

_VERSION_PY = "plfog/version.py"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VERSION_RE = re.compile(r'^VERSION = "([^"]+)"', re.MULTILINE)


def extract_version(source: str) -> str | None:
    """The ``VERSION`` literal from a ``plfog/version.py`` source text, or ``None`` if absent.

    Reading the literal rather than importing the module lets both sides of the comparison
    be parsed the same way — the previous side is a blob out of ``git show`` and cannot be
    imported at all.
    """
    match = _VERSION_RE.search(source)
    return match.group(1) if match else None


def should_fail(*, version_py_changed: bool, previous: str | None, current: str) -> bool:
    """Whether this push shipped a release that will announce nothing.

    True only when the push edited ``plfog/version.py`` and left ``VERSION`` identical. Two
    cases deliberately do not fail:

    - ``plfog/version.py`` untouched. The workflow's ``paths:`` filter already means it does
      not run, and that filter is load-bearing: the broader "``VERSION`` unchanged on any
      push" predicate would have fired 41 times in the same history, almost all of them on
      deliberate batched releases.
    - ``previous`` unknown — the ref is gone, or the file did not exist yet. That is not
      evidence of a lost announcement, so it falls through to the announcement, which is
      what this step did before the guard existed.
    """
    return version_py_changed and previous is not None and current == previous


def _git(*args: str) -> str | None:
    """Run a git command, returning its stdout, or ``None`` when git could not answer.

    A failure here means the question is unanswerable (an unreachable ref, a missing file at
    that ref), not that the answer is "no". Callers turn ``None`` into the permissive
    outcome so a guard that cannot see the base never blocks a legitimate release.
    """
    try:
        completed = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _current_version() -> str:
    """``VERSION`` as it stands in the checked-out tree.

    Resolved from ``__file__`` rather than the working directory, so it does not depend on
    where the workflow or the test runner was invoked from.
    """
    source = (_REPO_ROOT / _VERSION_PY).read_text(encoding="utf-8")
    current = extract_version(source)
    if current is None:
        sys.exit(f"Could not read VERSION from {_VERSION_PY}. The release guard cannot run.")
    return current


def _previous_version(before: str) -> str | None:
    """``VERSION`` as it stood at ``before``, or ``None`` when that blob is unreadable."""
    source = _git("show", f"{before}:{_VERSION_PY}")
    return extract_version(source) if source is not None else None


def _version_py_changed(before: str, after: str) -> bool:
    """Whether ``plfog/version.py`` differs across the whole push range."""
    changed = _git("diff", "--name-only", before, after, "--", _VERSION_PY)
    return bool(changed and changed.strip())


def _write_output(name: str, value: str) -> None:
    """Append a step output for the workflow to read."""
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _failure_message(*, after: str, previous: str, current: str) -> str:
    """What a maintainer reads when the guard fires. Names the commit and both versions."""
    return (
        f"Release guard: commit {after} edited {_VERSION_PY} but left VERSION at {current} "
        f"(it was {previous} before this push). The release deployed and the Discord "
        f"announcement was skipped, so members heard nothing about what just went live.\n"
        f"\n"
        f"Recovery, once the changelog entry for this release is correct on main:\n"
        f"  gh workflow run discord-notify.yml   posts the newest changelog entry to Discord\n"
        f"  python manage.py announce_release    sends the release email\n"
        f"\n"
        f"announce_release is idempotent per version: it records an EventDelivery row with\n"
        f'period="release:{current}" and a second run for that version sends nothing. Clear\n'
        f"that row first if a corrected announcement has to go out at the same version."
    )


def main() -> None:
    if os.environ["EVENT_NAME"] == "workflow_dispatch":
        # The documented recovery lever. A manual run always announces, whatever VERSION says
        # — that is the whole point of it, and the guard must never stand in its way.
        print("Manual run, announcing regardless of VERSION.")
        _write_output("should_post", "true")
        return

    before = os.environ["BEFORE_SHA"]
    after = os.environ["AFTER_SHA"]
    current = _current_version()
    previous = _previous_version(before)
    changed = _version_py_changed(before, after)
    print(
        f"push {before}..{after}  previous={previous or '<unknown>'}  current={current}  "
        f"{_VERSION_PY} changed={changed}"
    )

    if should_fail(version_py_changed=changed, previous=previous, current=current):
        # should_fail is False whenever previous is None, so the narrowing below is sound.
        assert previous is not None
        print(f"::error title=Release shipped without an announcement::commit {after} left VERSION at {current}")
        sys.exit(_failure_message(after=after, previous=previous, current=current))

    _write_output("should_post", "true")


if __name__ == "__main__":
    main()
