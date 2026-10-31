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
their date. The frozen entries in ``changelog/history.json`` keep the version numbers they
shipped under; ``templates/includes/changelog_modal.html`` renders the badge only when one is
present.

TOML rather than YAML because ``tomllib`` is stdlib: the release workflows install nothing
beyond the interpreter, so a YAML fragment would parse locally and fail in CI.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import tomllib
from dataclasses import dataclass
from typing import Any

from core.guidance import GUIDANCE
from core.help_registry import KEY_PATTERN

#: The bump levels a fragment may declare, weakest first. A PR picks exactly one.
BUMPS = ("patch", "minor", "major")

#: The two strings ``audience`` has always taken. ``members`` fragments become changelog entries
#: and get announced. ``internal`` ones move the version and announce nothing — the
#: tooling-release case that used to be a paragraph of prose in CLAUDE.md and a judgement call
#: every time.
AUDIENCE_STRINGS = ("members", "internal")

#: Every audience key a list may name, each with the What's New tab it rolls up to (#1007): the
#: three tabs themselves, the finer guild roles under Guild Staff, and instructors under Members.
#: ``internal`` is not here on purpose: an internal fragment is no entry, so it cannot share a
#: list with anyone.
AUDIENCES: dict[str, str] = {
    "members": "members",
    "guild-staff": "guild-staff",
    "admins": "admins",
    "guild-leads": "guild-staff",
    "orientors": "guild-staff",
    "treasurers": "guild-staff",
    "class-reviewers": "guild-staff",
    "equipment-managers": "guild-staff",
    "instructors": "members",
}

#: How much teaching a change ships (#1007), least first. Anything past ``none`` names a
#: ``feature`` registered in :mod:`core.guidance`; ``docs/GUIDANCE.md`` says what each ships.
GUIDANCE_LEVELS = ("none", "link", "clip", "tour")

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
    #: Feedback request numbers (``core.models.FeedbackRequest`` pks) this change delivers
    #: (#693). Read only by the ``announce_live_requests`` job; never part of :meth:`as_entry`,
    #: so no announcement or changelog shows them.
    requests: tuple[int, ...] = ()
    #: Who the entry is for (#1007): keys from :data:`AUDIENCES` in the order written, once
    #: each. A plain ``audience = "members"`` is ``("members",)``; an internal fragment has none.
    audiences: tuple[str, ...] = ()
    #: The feature this change belongs to, a key in :data:`core.guidance.GUIDANCE`, or ``""``.
    feature: str = ""
    #: How much teaching ships with it, one of :data:`GUIDANCE_LEVELS`.
    guidance: str = "none"
    #: The control the change lands on (#1051): a :data:`core.help_registry.KEY_PATTERN` key the
    #: same PR places on it as ``data-guidance-target``, or ``""``. Open <feature> adds it as
    #: ``?show=<key>`` and the page rings the control on arrival.
    target: str = ""

    @property
    def slug(self) -> str:
        """The fragment's filename stem: the stable id of its changelog entry."""
        return self.path.stem

    @property
    def is_member_facing(self) -> bool:
        """Whether this fragment becomes a changelog entry and an announcement."""
        return self.audience == "members"

    def as_entry(self) -> dict[str, Any]:
        """The fragment as a ``CHANGELOG`` entry, shaped like the frozen historical ones.

        No ``version`` key: see the module docstring. ``screenshot`` is omitted when unset so
        the entry matches a legacy entry that never had one, and
        ``core.release_email.build_release_cards`` keeps treating it as genuinely optional.
        ``slug`` lets a link open this entry in the changelog, and the sweep keeps it.
        ``requests`` is deliberately absent: request numbers are bookkeeping, not news.
        ``audiences``, ``feature`` and ``guidance`` are what What's New sorts and links by
        (#1007); a frozen entry from before them has none, so a reader treats them as optional.
        ``target`` (#1051) is written only when set, like ``screenshot``.
        """
        entry: dict[str, Any] = {
            "date": self.date,
            "title": self.title,
            "changes": list(self.changes),
            "slug": self.slug,
            "audiences": list(self.audiences),
            "feature": self.feature,
            "guidance": self.guidance,
        }
        if self.screenshot:
            entry["screenshot"] = self.screenshot
        if self.target:
            entry["target"] = self.target
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

    audience, audiences = _audiences(data, path)

    unknown = set(data) - {
        "bump",
        "audience",
        "date",
        "title",
        "changes",
        "screenshot",
        "requests",
        "feature",
        "guidance",
        "target",
    }
    if unknown:
        # A misspelled key is a bullet that silently never reaches members. Cheaper to
        # reject here than to notice it missing from a Discord post nobody can unsend.
        raise FragmentError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")

    if audience == "internal":
        for key in ("requests", "feature", "guidance", "target"):
            if key in data:
                # An internal release is announced nowhere, so it has no changelog entry to
                # link, sort or point at a feature.
                raise FragmentError(f"{path}: '{key}' needs a members fragment, not an internal one")
        return Fragment(path=path, bump=bump, audience=audience, date="", title="", changes=(), screenshot="")

    date, title, changes, screenshot = _member_fields(data, path)
    feature, guidance = _guidance(data, path)
    return Fragment(
        path=path,
        bump=bump,
        audience=audience,
        date=date,
        title=title,
        changes=changes,
        screenshot=screenshot,
        requests=_requests(data, path),
        audiences=audiences,
        feature=feature,
        guidance=guidance,
        target=_target(data, path, feature),
    )


def _audiences(data: dict[str, Any], path: pathlib.Path) -> tuple[str, tuple[str, ...]]:
    """The ``audience`` key as ``(string form, audience keys)`` (#1007).

    A string keeps meaning what it always has. A list names audience keys from
    :data:`AUDIENCES`, deduplicated in the order written, and its string form is ``members``:
    a listed fragment is an entry, whoever it is for. ``internal`` cannot sit in a list.
    """
    value = data.get("audience", "members")
    if isinstance(value, str):
        if value not in AUDIENCE_STRINGS:
            raise FragmentError(
                f"{path}: 'audience' must be one of {', '.join(AUDIENCE_STRINGS)}, "
                f"or a list of {', '.join(AUDIENCES)} (got {value!r})"
            )
        return value, (("members",) if value == "members" else ())
    if not isinstance(value, list) or not value:
        raise FragmentError(f"{path}: 'audience' must be a string or a non-empty list of audience keys")
    for key in value:
        if not isinstance(key, str):
            raise FragmentError(
                f"{path}: every entry in 'audience' must be a string naming an audience key "
                f"(got {key!r}); see changelog.d/README.md"
            )
        if key == "internal":
            raise FragmentError(f"{path}: 'audience' cannot list 'internal'; an internal fragment names it alone")
        if key not in AUDIENCES:
            raise FragmentError(
                f"{path}: every entry in 'audience' must be one of {', '.join(AUDIENCES)} (got {key!r})"
            )
    return "members", tuple(dict.fromkeys(value))


def _guidance(data: dict[str, Any], path: pathlib.Path) -> tuple[str, str]:
    """The optional ``feature`` and ``guidance`` keys, validated (#1007).

    ``feature`` must be registered in :data:`core.guidance.GUIDANCE`, so an entry can never
    offer Open it on a page that does not exist. ``guidance`` defaults to ``none`` and needs a
    feature past that, because a link, a clip or a tour has to belong to something. Whether a
    registered clip or tour actually exists for the level is ``check_changelog_fragment.py``'s
    question, not this one's: this runs at app import, and a missing clip must not stop the app.
    """
    feature = data.get("feature", "")
    if not isinstance(feature, str) or (feature and feature not in GUIDANCE):
        raise FragmentError(f"{path}: 'feature' must be a key registered in core/guidance.py (got {feature!r})")
    guidance = data.get("guidance", "none")
    if guidance not in GUIDANCE_LEVELS:
        raise FragmentError(f"{path}: 'guidance' must be one of {', '.join(GUIDANCE_LEVELS)} (got {guidance!r})")
    if guidance != "none" and not feature:
        raise FragmentError(f"{path}: 'guidance' = {guidance!r} needs a 'feature' to point at")
    return feature, guidance


def _target(data: dict[str, Any], path: pathlib.Path, feature: str) -> str:
    """The optional ``target`` key (#1051): the control the change lands on, a :data:`KEY_PATTERN` key.

    The same PR places the key on that control as ``data-guidance-target``; whether a template
    carries it is ``tests/hub/guidance_targets_spec.py``'s question, as help keys are held. It
    needs a ``feature``, because the feature's page is where ``?show=<key>`` is added.
    """
    target = data.get("target", "")
    if not isinstance(target, str) or (target and not KEY_PATTERN.match(target)):
        raise FragmentError(f"{path}: 'target' must be a key like area.control (got {target!r})")
    if target and not feature:
        raise FragmentError(f"{path}: 'target' = {target!r} needs a 'feature' whose page it is on")
    return target


def _requests(data: dict[str, Any], path: pathlib.Path) -> tuple[int, ...]:
    """The optional ``requests`` list: feedback request numbers this change delivers (#693).

    Each must be a positive integer, and the list must not repeat one. ``bool`` is excluded
    explicitly because TOML ``true`` parses to a Python ``bool``, which is an ``int``.
    """
    if "requests" not in data:
        return ()
    requests = data["requests"]
    if not isinstance(requests, list) or not requests:
        raise FragmentError(f"{path}: 'requests' must be a non-empty list of request numbers, like [12, 15]")
    for number in requests:
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise FragmentError(f"{path}: every entry in 'requests' must be a positive whole number (got {number!r})")
    if len(set(requests)) != len(requests):
        raise FragmentError(f"{path}: 'requests' lists the same request number more than once")
    return tuple(requests)


def _is_calendar_date(value: str) -> bool:
    """Whether ``value`` names a day that exists, so ``2026-02-30`` fails here, not on a page."""
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _member_fields(data: dict[str, Any], path: pathlib.Path) -> tuple[str, str, tuple[str, ...], str]:
    """The prose an ``audience = "members"`` fragment must carry, validated.

    Split out of :func:`parse_fragment` to keep both under the repo's complexity ceiling, and
    because these are the checks that only apply once a fragment claims it will be published.
    """
    date = _require_str(data, "date", path)
    if not _DATE_RE.match(date) or not _is_calendar_date(date):
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
    which is the real shape of this repo (two majors in its life).

    **Two majors in one unswept set is approximated rather than rejected**, and the reason is
    worth keeping. Being exact there would need to know how many minors fell between the two
    majors, which the tree does not record. An earlier draft raised on the case — but this
    function runs at import of ``plfog.version``, so raising means **the app does not boot**,
    and the case is reachable without anyone doing anything wrong: two PRs each declaring
    ``major`` both pass the PR check on their own (each sees only its own branch) and collide
    only once both are on main. Trading a production outage for an exact version number is the
    wrong way round. Every major still increments ``major``, so the result stays deterministic
    and monotonic; the only cost is that a minor which landed *between* two majors is counted
    after the last one. Sweep ``changelog.d/`` when you cut a major and the question does not
    arise.
    """
    major, minor, patch = parse_version(base)
    counts = {level: sum(1 for f in fragments if f.bump == level) for level in BUMPS}

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
    """Every release frozen by the last sweep, newest first.

    Read-only history. New changes are fragments; this file is appended to only by a sweep.

    A swept entry keeps the ``feature``, ``guidance`` and ``audiences`` its fragment declared
    (#1007), and they are checked here the way :func:`parse_fragment` checks a fragment's: a
    feature key the registry has since dropped, a level or an audience key nobody knows, fails
    at import, where a release is noticed, rather than in a member's What's New. A ``target``
    (#1051) is held to :data:`KEY_PATTERN` the same way.
    """
    entries: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    for entry in entries:
        where = f"{path}: entry {entry['title']!r} ({entry['date']})"
        feature = entry.get("feature", "")
        if feature and feature not in GUIDANCE:
            raise FragmentError(f"{where} names feature {feature!r}, which core/guidance.py does not register")
        if entry.get("guidance", "none") not in GUIDANCE_LEVELS:
            raise FragmentError(f"{where}: 'guidance' must be one of {', '.join(GUIDANCE_LEVELS)}")
        for key in entry.get("audiences", ()):
            if key not in AUDIENCES:
                raise FragmentError(f"{where} names audience {key!r}, not one of {', '.join(AUDIENCES)}")
        target = entry.get("target", "")
        if target and not KEY_PATTERN.match(target):
            raise FragmentError(f"{where} names target {target!r}, not a key like area.control")
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
