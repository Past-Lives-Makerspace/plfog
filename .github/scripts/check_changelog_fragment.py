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

A fragment's ``guidance`` is held to what exists here rather than in ``parse_fragment``
(#1007, #1050): ``tour`` needs a tour key on the feature's ``core.guidance`` entry, and every
``[[shots]]`` block of a ``shots`` or ``tour`` fragment needs its picture on the media bucket,
proved by a HEAD of ``R2_PUBLIC_URL/guidance/<slug>/step-N.png``. The parser runs at app
import, and a missing tour or picture must cost a red X on the branch, never a production that
does not boot. ``R2_PUBLIC_URL`` is a repository variable (a public address); unset, as on a
fork or a local run, the HEAD step is skipped and says so.

Stdlib only, and no Django: this runs in ci.yml's own `changelog` job, which installs
nothing beyond the interpreter so a missing fragment is reported in seconds rather than
after a dependency install.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, ".")
from core.guidance import GUIDANCE  # noqa: E402
from plfog.changelog import SHOT_LEVELS, Fragment, FragmentError, load_fragments  # noqa: E402
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


def missing_materials(fragments: list[Fragment]) -> list[str]:
    """One line per fragment whose ``guidance = "tour"`` names a feature with no tour registered.

    The parser already proved ``feature`` is registered, that a level past ``none`` has one and
    that ``shots`` and ``tour`` list their ``[[shots]]`` (#1050), so only the registered entry's
    ``tour`` is in question here; whether the pictures exist is :func:`missing_shots`' question.
    """
    problems: list[str] = []
    for fragment in fragments:
        if fragment.guidance == "tour" and GUIDANCE[fragment.feature].tour is None:
            problems.append(
                f"changelog.d/{fragment.path.name}: guidance = \"tour\" but '{fragment.feature}' "
                f"has no tour registered in core/guidance.py."
            )
    return problems


#: What the HEAD calls itself. The bucket's public host answers Python's default agent with a
#: 403 whatever the key (its bot filter), so the check names itself the way a browser would.
_USER_AGENT = "Mozilla/5.0 (compatible; plfog-fragment-check/1; +https://github.com/Past-Lives-Makerspace/plfog)"


def _head(url: str) -> int:
    """The status a HEAD of ``url`` answers; a 4xx or 5xx is returned as its code, not raised.

    A host that cannot be reached at all exits naming it: neither "missing" nor "fine" is an
    answer to a network failure.
    """
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except urllib.error.URLError as error:
        sys.exit(f"Could not reach {url}: {error.reason}")


def missing_shots(fragments: list[Fragment], public_url: str) -> list[str]:
    """One line per fragment at ``shots`` or ``tour`` naming its first picture the bucket does not answer 200 for (#1050).

    Every fragment in the tree is checked, not only the added ones: the pictures are permanent,
    so a fragment that passed once keeps passing, and one whose shots were deleted by hand is
    caught before the entry shows a broken picture.
    """
    problems: list[str] = []
    for fragment in fragments:
        if fragment.guidance not in SHOT_LEVELS:
            continue
        for key in fragment.shot_keys:
            url = f"{public_url}/{key}"
            status = _head(url)
            if status != 200:
                problems.append(
                    f"changelog.d/{fragment.path.name}: {url} answered {status}. Upload the shots with "
                    f"scripts/upload_screenshot.py --guidance {fragment.slug} step-1.png ... (docs/GUIDANCE.md)."
                )
                break
    return problems


def _labels() -> set[str]:
    """Labels on the pull request, from the event payload the workflow forwards.

    Defaulted rather than read as ``os.environ[...]`` like the shas are, and deliberately: an
    absent value means no label, which fails toward REQUIRING a fragment. The fail-loudly rule
    exists to stop a missing value quietly weakening a check, and this one cannot.
    """
    raw = os.environ.get("PR_LABELS", "[]")
    return {str(label).strip().lower() for label in json.loads(raw)}


def main() -> None:
    try:
        fragments = load_fragments(FRAGMENTS_PATH)
    except FragmentError as exc:
        sys.exit(f"Invalid changelog fragment.\n\n{exc}\n\nSee changelog.d/README.md.")

    # Parsing the whole directory is also what computes VERSION, so importing it above has
    # already proved the fold works and that every fragment in the tree parses.
    print(f"{len(fragments)} fragment(s) in changelog.d/, folding to v{VERSION}.")

    materials = missing_materials(fragments)
    if materials:
        sys.exit("A fragment promises guidance its feature has not registered.\n\n" + "\n".join(materials))

    # Defaulted on purpose, like PR_LABELS: a fork or a local run has no repository variable, and
    # skipping the HEAD step out loud there is the documented behaviour, not a weakened check.
    public_url = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    if public_url:
        shots = missing_shots(fragments, public_url)
        if shots:
            sys.exit("A fragment promises shots that are not on the bucket.\n\n" + "\n".join(shots))
    else:
        print(
            "R2_PUBLIC_URL is unset; not checking that each fragment's shots are on the bucket (CI sets it from the repository variable)."
        )

    if _SKIP_LABEL in _labels():
        print(f"'{_SKIP_LABEL}' label present; not requiring a fragment.")
        return

    changed = _changed_fragments(os.environ["BASE_SHA"], os.environ["HEAD_SHA"])
    if not changed:
        # The defaults below are deliberate, unlike the os.environ[...] reads above. These two
        # only fill in the example fragment inside a message that is already an error, so a
        # missing one costs a less specific suggestion rather than hiding a bug; failing here
        # would replace a useful message with a traceback.
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
