"""Fail the release run when a push leaves ``VERSION`` where it was.

A push to ``main`` that edits ``plfog/version.py`` but leaves ``VERSION`` exactly where it
was deploys to production and tells nobody: ``discord-notify.yml`` used to set
``should_post=false`` and exit zero, so the Actions tab stayed green and the missing
announcement was invisible until somebody noticed. That happened on the merge that shipped
the lobby slideshow (``11a1693d``, v1.49.0, #348), and once before it on the handwritten
repair of the same bug (``920fab64``, v0.23.39). Replayed over every first-parent commit on
``main`` that touched ``plfog/version.py``, those two are the only ones this guard would
have failed: 2 of 222.

So the skip is now a non-zero exit. **A red X on the Actions tab is the whole alert**: no
Discord ping, no auto-filed issue, no notification of any kind outside GitHub. That is a
deliberate decision, not an omission.

**What this does NOT catch**, and the gaps are larger than the thing it does catch:

- **A merge that never touches ``plfog/version.py`` at all.** The workflow's ``paths:`` filter
  means it does not run, so there is not even a run to go red. That is the ordinary "forgot to
  bump" mistake, and it is deliberately out of reach: the broader predicate would have fired on
  41 more commits in the same history, almost all deliberate batched releases.
- **A release that moves ``VERSION`` but stamps its entry at the wrong number.** It deploys,
  announces nothing, and goes green — ``discord_release_notify.py`` prints "nothing
  member-facing to announce" and exits 0, which is *also* the correct behaviour for a tooling
  release that deliberately carries no entry. The two are indistinguishable from here.

So this closes the #348 shape — edited the file, left the literal — and nothing wider.

The comparison base is ``github.event.before`` — the tip of ``main`` before the push — not
``HEAD^``. A rebase merge pushes every commit of the PR at once, and ``HEAD^`` is then a
commit from inside that PR, which may already carry the bump. ``discord-notify.yml`` checks
out with ``fetch-depth: 0`` so that base is always reachable, and this script **fails loudly
if it is not**: a guard that cannot see the base and shrugs is the #348 bug wearing a hat.

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
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_NULL_SHA = "0" * 40


def extract_version(source: str) -> str | None:
    """The ``VERSION`` literal from a ``plfog/version.py`` source text, or ``None`` if absent.

    Takes the **last** top-level assignment, which is what Python itself would bind. A first-
    match read would disagree with ``discord_release_notify.py``, which gets its ``VERSION``
    by importing the module: a stray duplicate literal above the real one would then let the
    guard compare one number while the announcement posts at another.

    Reading the literal rather than importing lets both sides of the comparison be parsed the
    same way. The previous side is a blob out of ``git show`` and cannot be imported at all.
    """
    matches = _VERSION_RE.findall(source)
    return matches[-1] if matches else None


def should_fail(*, version_py_changed: bool, previous: str | None, current: str) -> bool:
    """Whether this push shipped a release that will announce nothing.

    True only when the push edited ``plfog/version.py`` and left ``VERSION`` identical. Two
    cases deliberately do not fail:

    - ``plfog/version.py`` untouched. The workflow's ``paths:`` filter already means it does
      not run, and that filter is load-bearing: the broader "``VERSION`` unchanged on any
      push" predicate would have fired on 41 *more* commits in the same history, almost all of
      them deliberate batched releases.
    - ``previous`` unknown, meaning ``plfog/version.py`` did not exist at the base commit.
      That is the repo's earliest history, not a lost announcement. It is NOT the same as
      "the base commit is missing from the clone", which fails the run instead — see
      ``_require_readable_base``.
    """
    return version_py_changed and previous is not None and current == previous


def _git(*args: str) -> str | None:
    """Run a git command in the repo root, returning stdout, or ``None`` on a non-zero exit.

    ``None`` means "git declined to answer this question", and every caller says in its own
    docstring what it does with that. An ``OSError`` — git missing or unexecutable — is NOT
    caught: that is a broken guard rather than an unanswerable question, and it should crash
    the step rather than degrade into a pass.
    """
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
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


def _names_a_commit(before: str) -> bool:
    """Whether ``github.event.before`` names a commit at all.

    It is forty zeroes when the push created the branch, and empty on event payloads that
    carry no base. Neither is a commit, and neither is evidence of a lost announcement.

    ``fullmatch`` rather than an anchored ``match``: with ``^…$`` in the pattern the leading
    anchor and the ``match`` call each made the other redundant, so neither could be specced
    on its own and dropping BOTH — two edits, but two edits nothing would have caught —
    accepted a sha with junk in front of it.
    """
    return bool(_SHA_RE.fullmatch(before)) and before != _NULL_SHA


def _require_readable_base(before: str) -> None:
    """Fail the run when the base names a commit this clone does not have.

    This is the hole that would otherwise swallow the whole guard. If the checkout loses
    history — someone lowers ``fetch-depth``, bumps the checkout action, or ``main`` is
    force-pushed so the old tip is orphaned — then ``git show`` and ``git diff`` both fail,
    ``previous`` is unknown, ``version_py_changed`` is false, and the guard passes green on
    exactly the push it exists to catch. Silence there is indistinguishable from #348.
    """
    if _git("cat-file", "-e", f"{before}^{{commit}}") is None:
        sys.exit(
            f"Release guard: the push base {before} is not in this clone, so VERSION cannot be "
            f"compared against it and this guard would pass on a release that announces nothing.\n"
            f"\n"
            f"Check that .github/workflows/discord-notify.yml still checks out with fetch-depth: 0. "
            f'Then read "The release guard" in CLAUDE.md and judge this push by hand.'
        )


def _previous_version(before: str) -> str | None:
    """``VERSION`` as it stood at ``before``, or ``None`` when the file did not exist there.

    ``before`` is known to be a commit in this clone by the time this runs, so a failure here
    means the path was absent at that commit, not that the ref is missing.
    """
    source = _git("show", f"{before}:{_VERSION_PY}")
    return extract_version(source) if source is not None else None


def _version_py_changed(before: str, after: str) -> bool:
    """Whether ``plfog/version.py`` differs across the whole push range."""
    changed = _git("diff", "--name-only", before, after, "--", _VERSION_PY)
    if changed is None:
        sys.exit(f"Release guard: could not diff {before}..{after}. The release guard cannot run.")
    return bool(changed.strip())


def _write_output(name: str, value: str) -> None:
    """Append a step output for the workflow to read."""
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _failure_message(*, after: str, previous: str, current: str) -> str:
    """What a maintainer reads when the guard fires: the fact, the lever, and one caution.

    Deliberately this short. Three review rounds each rewrote a longer runbook here, and each
    rewrite encoded a rule for deciding whether members had already seen the entry at
    ``current`` — first "does the entry read well", then "did this push write it", then "did
    an entry exist at the base". Every one of them was satisfiable by a shape nobody had
    enumerated, and every one of them therefore sent a maintainer to a manual run that
    re-announced a release members had already had. A Discord post cannot be unsent, so the
    rule is gone rather than written a fourth time.

    What this message owes the maintainer is the fact, the one lever, and the reason to look
    before pulling it. The judgment is theirs. The rest is in CLAUDE.md, which is a better
    place for it than a CI log.
    """
    return (
        f"Release guard: commit {after} edited {_VERSION_PY} but left VERSION at {current} "
        f"(it was {previous} before this push). The release deployed and the Discord "
        f"announcement was skipped, so members heard nothing about what just went live.\n"
        f"\n"
        f"The lever is a manual run, which posts whatever VERSION says and which this guard\n"
        f"never blocks:\n"
        f"  gh workflow run discord-notify.yml\n"
        f"\n"
        f"Look before you pull it. This guard fires on any {_VERSION_PY} edit that leaves\n"
        f"VERSION alone, including a reflow or a typo fix on an entry an earlier release\n"
        f"already announced, so check whether members have already seen the entry at\n"
        f"{current}. Re-posting one they have seen announces the wrong release, and a Discord\n"
        f"post cannot be unsent. Nothing may be owed at all.\n"
        f"\n"
        f'Read "The release guard" in CLAUDE.md before reaching for announce_release: it posts\n'
        f"to Discord as well as sending the email, so on top of the command above it announces\n"
        f"the same release twice."
    )


def main() -> None:
    """Arm the announcement, or fail the run.

    Reads ``EVENT_NAME``, ``BEFORE_SHA`` and ``AFTER_SHA`` from the environment (the workflow
    sets them from ``github.event_name``, ``github.event.before`` and ``github.sha``) and
    writes ``should_post`` to ``GITHUB_OUTPUT``.

    There is no ``should_post=false``. A push that would once have been skipped is now a
    failure, and a manual run always posts, so every path that returns has armed the post
    step and every path that has not, exited non-zero.
    """
    if os.environ["EVENT_NAME"] == "workflow_dispatch":
        # The documented recovery lever. A manual run always announces, whatever VERSION says
        # — that is the whole point of it, and the guard must never stand in its way.
        print("Manual run, announcing regardless of VERSION.")
        _write_output("should_post", "true")
        return

    before = os.environ["BEFORE_SHA"]
    after = os.environ["AFTER_SHA"]
    current = _current_version()

    if not _names_a_commit(before):
        # A branch creation pushes forty zeroes. There is no previous release to compare
        # against, so there is no lost announcement to detect: announce, as this step did
        # before the guard existed.
        print(f"No push base to compare against (before={before or '<empty>'}), announcing.")
        _write_output("should_post", "true")
        return

    _require_readable_base(before)
    previous = _previous_version(before)
    changed = _version_py_changed(before, after)
    print(
        f"push {before}..{after}  previous={previous or '<none>'}  current={current}  {_VERSION_PY} changed={changed}"
    )

    if should_fail(version_py_changed=changed, previous=previous, current=current):
        # should_fail is False whenever previous is None, so the narrowing below is sound.
        assert previous is not None
        print(f"::error title=Release shipped without an announcement::commit {after} left VERSION at {current}")
        sys.exit(_failure_message(after=after, previous=previous, current=current))

    _write_output("should_post", "true")


if __name__ == "__main__":
    main()
