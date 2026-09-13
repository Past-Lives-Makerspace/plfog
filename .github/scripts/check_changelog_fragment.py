"""Fail a pull request that ships no changelog fragment, and reject a malformed one.

This is what actually replaced ``release_guard.py``. The guard ran on ``main``, after the
merge, after Render had already deployed, and it said so itself: "a merge that never touches
``plfog/version.py`` at all does not fail anything... that is the ordinary forgot-to-bump
mistake, and it is out of reach on purpose." It was out of reach because the predicate it
would have needed — "this push should have moved the version" — was unknowable from a diff.

With one fragment per PR it is knowable, and it is knowable on the branch. A PR either adds a
file to ``changelog.d/`` or carries the ``no-changelog`` label. Getting it wrong costs a red X
on a branch nobody has deployed, rather than a release members never hear about.

Validating every fragment in the tree (not only the added ones) is deliberate: a PR that
*edits* an unshipped fragment can break it just as thoroughly as one that adds a broken one,
and the release workflow parses the whole directory to fold the version.

Stdlib only, and no Django: this runs in the lint job before anything is installed.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, ".")
from plfog.changelog import FragmentError, load_fragments  # noqa: E402
from plfog.version import FRAGMENTS_PATH, VERSION  # noqa: E402

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The pathspec git is asked about, fixed at import — see release_plan.py for why it is not
#: recomputed from _REPO_ROOT at call time.
_PATHSPEC = f"{FRAGMENTS_PATH.relative_to(_REPO_ROOT).as_posix()}/*.toml"

_SKIP_LABEL = "no-changelog"

_MISSING = """No changelog fragment in this pull request.

Every PR adds exactly one file to changelog.d/. It is the only thing that moves the version,
and it is what gets announced when this merges. See changelog.d/README.md.

    changelog.d/{number}-short-slug.toml

    bump = "minor"          # patch | minor | major
    date = "{today}"
    title = "What members will see as the headline"
    changes = [
      "One plain-language bullet per thing a member can now do.",
    ]

Repo tooling, tests and refactors that members will never see take the same file with
`audience = "internal"` and nothing else — no title, no bullets. That still moves the version
and announces nothing.

If this PR genuinely ships no release at all (a README typo, a comment), add the
`{label}` label instead."""


def _changed_fragments(base: str, head: str) -> list[str]:
    """Fragment files this PR adds or modifies, relative to the merge base."""
    completed = subprocess.run(
        ["git", "diff", "--diff-filter=AM", "--name-only", f"{base}...{head}", "--", _PATHSPEC],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    if completed.returncode:
        sys.exit(f"Could not diff {base}...{head}: {completed.stderr.strip()}")
    return [line for line in completed.stdout.split() if line.strip()]


def _labels() -> set[str]:
    """Labels on the pull request, from the event payload the workflow forwards."""
    raw = os.environ.get("PR_LABELS", "[]")
    return {str(label).strip().lower() for label in json.loads(raw)}


def main() -> None:
    try:
        fragments = load_fragments(FRAGMENTS_PATH)
    except FragmentError as exc:
        sys.exit(f"Invalid changelog fragment.\n\n{exc}\n\nSee changelog.d/README.md.")

    # Parsing the whole directory is also what computes VERSION, so importing it above has
    # already proved the fold works — including the "two majors in one unswept set" rejection.
    print(f"{len(fragments)} fragment(s) in changelog.d/, folding to v{VERSION}.")

    if _SKIP_LABEL in _labels():
        print(f"'{_SKIP_LABEL}' label present; not requiring a fragment.")
        return

    changed = _changed_fragments(os.environ["BASE_SHA"], os.environ["HEAD_SHA"])
    if not changed:
        sys.exit(
            _MISSING.format(
                number=os.environ.get("PR_NUMBER", "123"),
                today=os.environ.get("TODAY", "2026-01-01"),
                label=_SKIP_LABEL,
            )
        )

    for path in changed:
        print(f"  {path}")


if __name__ == "__main__":
    main()
