"""Compose ``VERSION`` and ``CHANGELOG`` from per-change fragments.

**Why this exists.** Every PR used to edit two spots in one 3,412-line ``plfog/version.py``:
the ``VERSION`` literal at the top and a fifteen-line insertion at the head of ``CHANGELOG``.
Two PRs open at once collided at both. Measured over the 60 merges before this change, 15 of
them — 25% — had to resolve that file before merging, about 2.4 renumber events a week.

The fix is that **a PR never edits a shared file**. It adds ``changelog.d/<pr>-<slug>.toml``,
a filename nothing else claims, and declares one thing: whether the change is a ``patch``, a
``minor`` or a ``major``. Nothing in a PR names a version number, so nothing in a PR can go
stale on a rebase or collide with another PR's number.

**Why the version is folded rather than written.** It was believed that ``VERSION`` had to
stay a hand-edited literal because a workflow cannot push to ``main`` — which is true, the
ruleset on the default branch has no bypass actors and both ``GITHUB_TOKEN`` and ``BOT_PAT``
are rejected with ``GH013``. But the version does not have to live on the branch. Here it
lives nowhere at all: it is a pure function of the files on disk, so Render, the Hetzner QA
box, a local checkout and the release workflow all compute the same answer with no git, no
network and no race against the tagging workflow. ``.github/workflows/release.yml`` folds the
same numbers to decide which tag to push.

**Why the fold is order-independent.** Fragments accumulate; a merge only ever adds to the
set. Counting bumps rather than replaying them in sequence means a fragment's arrival can
never renumber a fragment that shipped before it, which is the property that makes a rebase
safe. See :func:`fold_version` for the one case that is not commutative and how it is handled.

**Why fragments carry no version of their own.** A fragment's *own* release number would need
merge order, and the tree does not record merge order — only git does, and only in CI. Rather
than encode a guess that drifts when a stale PR merges late, new entries are identified by
their date. The 258 frozen entries in ``changelog/history.json`` keep the version numbers they
shipped under; ``templates/includes/changelog_modal.html`` renders the badge only when one is
present.

TOML rather than YAML because ``tomllib`` is stdlib: the release workflows install nothing
beyond the interpreter, so a YAML fragment would parse locally and fail in CI.
"""

from __future__ import annotations

import json
import pathlib
import re
import tomllib
from dataclasses import dataclass
from typing import Any

#: The bump levels a fragment may declare, weakest first. A PR picks exactly one.
BUMPS = ("patch", "minor", "major")

#: ``members`` fragments become changelog entries and get announced. ``internal`` ones move
#: the version and announce nothing — the tooling-release case that used to be a paragraph of
#: prose in CLAUDE.md and a judgement call every time.
AUDIENCES = ("members", "internal")

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FragmentError(ValueError):
    """A fragment that cannot be trusted to build a release from.

    Raised eagerly, with the offending path in the message, rather than skipping the file:
    a fragment silently dropped for a typo is a release that ships and announces nothing,
    which is the exact failure (#348) that started this whole line of work.
    """


@dataclass(frozen=True)
class Fragment:
    """One unreleased change, as declared by the PR that made it."""

    path: pathlib.Path
    bump: str
    audience: str
    date: str
    title: str
    changes: tuple[str, ...]
    screenshot: str

    @property
    def is_member_facing(self) -> bool:
        """Whether this fragment becomes a changelog entry and an announcement."""
        return self.audience == "members"

    def as_entry(self) -> dict[str, Any]:
        """The fragment as a ``CHANGELOG`` entry, shaped like the frozen historical ones.

        No ``version`` key: see the module docstring. ``screenshot`` is omitted when unset so
        the entry matches a legacy entry that never had one, and
        ``core.release_email.build_release_cards`` keeps treating it as genuinely optional.
        """
        entry: dict[str, Any] = {
            "date": self.date,
            "title": self.title,
            "changes": list(self.changes),
        }
        if self.screenshot:
            entry["screenshot"] = self.screenshot
        return entry


def parse_version(version: str) -> tuple[int, int, int]:
    """``"1.62.1"`` → ``(1, 62, 1)``, raising :class:`ValueError` on anything else.

    Strict rather than lenient: a base version that does not parse means the fold below
    would invent a number, and an invented number is a mis-stamped release.
    """
    match = _VERSION_RE.match(version.strip())
    if match is None:
        raise ValueError(f"Not a MAJOR.MINOR.PATCH version: {version!r}")
    return int(match[1]), int(match[2]), int(match[3])


def _require_str(data: dict[str, Any], key: str, path: pathlib.Path) -> str:
    """A required non-empty string field, or :class:`FragmentError` naming the file."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FragmentError(f"{path}: '{key}' must be a non-empty string")
    return value.strip()


def parse_fragment(source: str, path: pathlib.Path) -> Fragment:
    """Parse and validate one fragment's TOML text.

    Split from :func:`load_fragment` so the validation can be specced without touching the
    filesystem. ``path`` is carried only to name the file in error messages.

    An ``internal`` fragment needs nothing but its ``bump``: it exists to move the version on
    a release that members will never see, so demanding a title and bullets for it would be
    demanding invented prose that gets published nowhere.
    """
    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise FragmentError(f"{path}: not valid TOML ({exc})") from exc

    bump = data.get("bump")
    if bump not in BUMPS:
        raise FragmentError(f"{path}: 'bump' must be one of {', '.join(BUMPS)} (got {bump!r})")

    audience = data.get("audience", "members")
    if audience not in AUDIENCES:
        raise FragmentError(f"{path}: 'audience' must be one of {', '.join(AUDIENCES)} (got {audience!r})")

    unknown = set(data) - {"bump", "audience", "date", "title", "changes", "screenshot"}
    if unknown:
        # A misspelled key is a bullet that silently never reaches members. Cheaper to
        # reject here than to notice it missing from a Discord post nobody can unsend.
        raise FragmentError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")

    if audience == "internal":
        return Fragment(path=path, bump=bump, audience=audience, date="", title="", changes=(), screenshot="")

    date, title, changes, screenshot = _member_fields(data, path)
    return Fragment(
        path=path,
        bump=bump,
        audience=audience,
        date=date,
        title=title,
        changes=changes,
        screenshot=screenshot,
    )


def _member_fields(data: dict[str, Any], path: pathlib.Path) -> tuple[str, str, tuple[str, ...], str]:
    """The prose an ``audience = "members"`` fragment must carry, validated.

    Split out of :func:`parse_fragment` to keep both under the repo's complexity ceiling, and
    because these are the checks that only apply once a fragment claims it will be published.
    """
    date = _require_str(data, "date", path)
    if not _DATE_RE.match(date):
        raise FragmentError(f"{path}: 'date' must be YYYY-MM-DD (got {date!r})")

    changes = data.get("changes")
    if not isinstance(changes, list) or not changes:
        raise FragmentError(f"{path}: 'changes' must be a non-empty list of strings")
    for change in changes:
        if not isinstance(change, str) or not change.strip():
            raise FragmentError(f"{path}: every entry in 'changes' must be a non-empty string")

    screenshot = data.get("screenshot", "")
    if not isinstance(screenshot, str):
        raise FragmentError(f"{path}: 'screenshot' must be a string when present")

    return (
        date,
        _require_str(data, "title", path),
        tuple(change.strip() for change in changes),
        screenshot.strip(),
    )


def load_fragment(path: pathlib.Path) -> Fragment:
    """Read and validate one fragment file."""
    return parse_fragment(path.read_text(encoding="utf-8"), path)


def load_fragments(directory: pathlib.Path) -> list[Fragment]:
    """Every fragment in ``directory``, newest first.

    A missing directory yields nothing: that is the state right after a sweep, not an error.

    The order is ``(date, filename)`` descending. Filename breaks a same-day tie so the list
    is stable across machines — ``Path.glob`` order is filesystem order, and a changelog that
    reshuffles between the developer's laptop and Render is a changelog nobody can review.
    Only the *display* order depends on this; :func:`fold_version` deliberately does not.
    """
    if not directory.is_dir():
        return []
    fragments = [load_fragment(path) for path in sorted(directory.glob("*.toml"))]
    return sorted(fragments, key=lambda f: (f.date, f.path.name), reverse=True)


def fold_version(base: str, fragments: list[Fragment]) -> str:
    """The version ``base`` becomes once every fragment has shipped.

    Counts bumps instead of replaying them one at a time, which is what makes the result
    independent of the order fragments arrive in — and therefore what makes a rebase safe.
    A merge only adds to the set, so a fragment landing today can never renumber one that
    shipped last week.

    Semver's reset semantics survive the counting: a major resets minor and patch, so with a
    major in the set the minor is *only* the minors and the patch is *only* the patches; a
    minor resets patch the same way. That is exact whenever at most one major is present,
    which is the real shape of this repo (two majors in its life). Two majors in one unswept
    set would need to know how many minors fell between them, so the case is rejected rather
    than approximated — sweep ``changelog.d/`` when you cut a major.
    """
    major, minor, patch = parse_version(base)
    counts = {level: sum(1 for f in fragments if f.bump == level) for level in BUMPS}

    if counts["major"] > 1:
        raise ValueError(
            f"{counts['major']} fragments declare bump = 'major' in one unreleased set. "
            f"Sweep changelog.d/ into changelog/history.json and move changelog/base.json "
            f"forward before cutting a second major."
        )
    if counts["major"]:
        return f"{major + counts['major']}.{counts['minor']}.{counts['patch']}"
    if counts["minor"]:
        return f"{major}.{minor + counts['minor']}.{counts['patch']}"
    return f"{major}.{minor}.{patch + counts['patch']}"


def load_base(path: pathlib.Path) -> str:
    """The version ``changelog/base.json`` folds forward from.

    Moved only by a sweep, never by a feature PR — which is the whole point: the one file
    that still carries a number is the one file a PR has no reason to touch.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data["version"]
    parse_version(version)  # fail here, not eight lines later inside the fold
    return str(version)


def load_history(path: pathlib.Path) -> list[dict[str, Any]]:
    """The frozen entries — 258 releases up to and including v1.62.1, newest first.

    Read-only history. New changes are fragments; this file is appended to only by a sweep.
    """
    entries: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return entries


def compose_changelog(history: list[dict[str, Any]], fragments: list[Fragment]) -> list[dict[str, Any]]:
    """Fragment entries (newest first) ahead of the frozen history.

    Fragments are always newer than everything frozen — a sweep is what moves a fragment into
    history, so an unswept fragment is by construction after the last swept one. That makes
    the concatenation newest-first end to end without a merge sort, and it keeps
    ``CHANGELOG[0]`` meaning "the most recent thing members were told about", which is what
    the changelog modal shows first.

    ``internal`` fragments are absent: they moved the version and are not news.
    """
    return [f.as_entry() for f in fragments if f.is_member_facing] + history
