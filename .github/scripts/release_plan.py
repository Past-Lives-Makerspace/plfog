"""Decide what this push released: the version to tag, and the entries to announce.

Replaces ``release_guard.py``. That guard existed to catch a release that deployed and
announced nothing — the #348 shape, where a push edited ``plfog/version.py`` and left the
``VERSION`` literal alone. There is no literal to leave alone any more: the version is folded
from ``changelog.d/``, so a push that adds a fragment moves it and a push that adds none does
not. The mistake the guard detected is no longer expressible.

What replaced the guard is ``check_changelog_fragment.py``, which fails the **pull request**
that adds no fragment. That is both earlier and wider: the old guard admitted in its own
docstring that "a merge that never touches ``plfog/version.py`` at all" was out of its reach,
which is the ordinary forgot-to-bump mistake. The PR check catches it before Render deploys
anything, and costs a red X on a branch instead of a missed announcement in production.

**What this announces is what this push added**, not what a number says. ``git diff
--diff-filter=A`` against the tip of ``main`` before the push names exactly the fragments that
went live in this deploy. Editing a fragment that already shipped therefore re-announces
nothing, which used to be a rule maintainers had to hold in their heads and is now a property
of the diff.

The comparison base is ``github.event.before``, not ``HEAD^``: a rebase merge pushes every
commit of the PR at once, so ``HEAD^`` is a commit from inside the PR. The workflow checks out
with ``fetch-depth: 0`` so that base is reachable, and this script **fails loudly when it is
not** — a planner that cannot see the base and shrugs would announce nothing and go green,
which is the failure it inherited responsibility for.

Stdlib only: the release workflow installs nothing beyond the interpreter.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, ".")
from plfog.changelog import Fragment, load_fragment, load_fragments  # noqa: E402
from plfog.version import FRAGMENTS_PATH, VERSION  # noqa: E402

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_NULL_SHA = "0" * 40

#: The pathspec git is asked about, fixed at import. Kept separate from ``_REPO_ROOT`` (which
#: only resolves the names git hands back) so that what the planner ASKS git and where it READS
#: the answer from are two independent facts a spec can pin one at a time.
_PATHSPEC = f"{FRAGMENTS_PATH.relative_to(_REPO_ROOT).as_posix()}/*.toml"


def _git(*args: str) -> str | None:
    """Run git in the repo root, returning stdout, or ``None`` on a non-zero exit.

    ``None`` means "git declined to answer"; every caller states what it does with that. An
    ``OSError`` is deliberately not caught — git missing is a broken workflow, not an
    unanswerable question, and it should crash rather than degrade into a silent pass.
    """
    completed = subprocess.run(["git", *args], capture_output=True, text=True, check=False, cwd=_REPO_ROOT)
    return None if completed.returncode else completed.stdout


def _names_a_commit(before: str) -> bool:
    """Whether ``github.event.before`` names a commit at all.

    Forty zeroes when the push created the branch, empty on payloads carrying no base.
    """
    return bool(_SHA_RE.fullmatch(before)) and before != _NULL_SHA


def _require_readable_base(before: str) -> None:
    """Fail when the base names a commit this clone does not have.

    If the checkout loses history — someone lowers ``fetch-depth``, or ``main`` is force-pushed
    so the old tip is orphaned — the diff below returns nothing, which is indistinguishable
    from "this push added no fragments". Silence there is a missed announcement.
    """
    if _git("cat-file", "-e", f"{before}^{{commit}}") is None:
        sys.exit(
            f"Release: the push base {before} is not in this clone, so the fragments this push "
            f"added cannot be identified and the announcement would be silently skipped.\n"
            f"Check that .github/workflows/release.yml still checks out with fetch-depth: 0."
        )


def added_fragments(before: str, after: str) -> list[Fragment]:
    """The fragments this push **added**, newest first.

    Added, not modified: a fragment edited after it shipped is a correction to the in-app
    changelog, and re-announcing it would post a release members already saw.
    """
    out = _git("diff", "--diff-filter=A", "--name-only", before, after, "--", _PATHSPEC)
    if out is None:
        sys.exit(f"Release: could not diff {before}..{after}. Refusing to guess what shipped.")
    paths = [_REPO_ROOT / line for line in out.split() if line.strip()]
    fragments = [load_fragment(path) for path in paths if path.is_file()]
    return sorted(fragments, key=lambda f: (f.date, f.path.name), reverse=True)


def _newest_member_facing() -> list[Fragment]:
    """The most recent member-facing fragment, for the manual re-announce escape hatch.

    Deliberately one, not all of ``changelog.d/``: the lever exists to re-send an
    announcement that failed to post, and a manual run that fired off every unswept fragment
    would spam members with a season of releases. Empty right after a sweep, and the workflow
    then says so rather than posting the wrong thing — a Discord post cannot be unsent.
    """
    return [f for f in load_fragments(FRAGMENTS_PATH) if f.is_member_facing][:1]


def _tag_exists(tag: str) -> bool:
    """Whether ``tag`` is already on origin. Re-running a workflow must not fail on its own tag."""
    out = _git("ls-remote", "--tags", "origin", f"refs/tags/{tag}")
    if out is None:
        sys.exit(f"Release: could not reach origin to check for {tag}.")
    return bool(out.strip())


def _write_outputs(**values: str) -> None:
    """Append step outputs for the workflow to read."""
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        for name, value in values.items():
            handle.write(f"{name}={value}\n")


def _plan_fragments() -> list[Fragment]:
    """The fragments to announce for this event, or an empty list for "announce nothing"."""
    if os.environ["EVENT_NAME"] == "workflow_dispatch":
        print("Manual run: re-announcing the newest member-facing fragment.")
        return _newest_member_facing()

    before = os.environ["BEFORE_SHA"]
    if not _names_a_commit(before):
        # A branch creation pushes forty zeroes. There is no previous tip, so no push range,
        # and nothing to call newly added.
        print(f"No push base (before={before or '<empty>'}); announcing nothing.")
        return []

    _require_readable_base(before)
    added = added_fragments(before, os.environ["AFTER_SHA"])
    if not added:
        # Not a failure: a PR carrying the no-changelog label legitimately adds none, and the
        # pull-request check is where the forgotten fragment is caught. Visible, not red.
        print("::warning title=No changelog fragment::This push added no fragment, so the version did not move.")
    return added


def main() -> None:
    fragments = _plan_fragments()
    member_facing = [f for f in fragments if f.is_member_facing]
    tag = f"v{VERSION}"

    payload = [f.as_entry() for f in member_facing]
    announce_path = pathlib.Path(os.environ.get("RUNNER_TEMP", ".")) / "announce.json"
    announce_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"version={VERSION} tag={tag} added={len(fragments)} member_facing={len(member_facing)}")
    for fragment in fragments:
        print(f"  {fragment.path.name}  bump={fragment.bump}  audience={fragment.audience}")

    _write_outputs(
        version=VERSION,
        tag=tag,
        tag_exists=str(_tag_exists(tag)).lower(),
        announce=str(bool(member_facing)).lower(),
        announce_path=str(announce_path),
    )


if __name__ == "__main__":
    main()
