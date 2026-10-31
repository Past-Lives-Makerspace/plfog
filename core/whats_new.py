"""What's New for one viewer (#1007, part 2): which entries, on which tab, and what they have seen.

The entries are ``plfog.version.CHANGELOG`` as it stands (D1: fragments and ``history.json``,
no model), every release, newest first, a page of :data:`PAGE_SIZE` at a time. Each one names
its ``audiences`` (D2; a frozen entry from before the keys is for ``members``), and a tab lists
every entry whose audience rolls up to it (:data:`plfog.changelog.AUDIENCES`), to every member,
as the changelog modal shows everything to everyone (D8). Who the viewer is comes from
``request.view_as`` (D5), in one query however many roles there are to ask about, and decides
only two things: whether Open it shows on an entry and whether the entry counts as unseen.

Unseen is bounded by the window: an entry dated inside the last :data:`NEW_DAYS` days, in the
viewer's audiences, that they have not looked at. Seen state is ``Member.whats_new_seen``, a
list of entry slugs pruned to the window on every write (D6); it drives the badges and the
modal's default tab. The What's New button has its own list, ``Member.whats_new_opened``: the
entries What's New had on offer when the viewer last opened it, listed on the page shown or not,
so one open quiets the button and a release lights it again (Felix, 2026-10-29). One
:class:`WhatsNew` per request, built on first use by :func:`whats_new_for`, so the Home button's
dot, the modal and part 3's badges share the roles query (D9).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from itertools import groupby
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from core.guidance import GUIDANCE, covers, url_for
from plfog.changelog import AUDIENCES, SHOT_LEVELS, Shot, shot_key

if TYPE_CHECKING:
    from django.http import HttpRequest

    from membership.models import Member

#: How long an entry counts as new: unseen until looked at, the dot on the button, badged (D6).
NEW_DAYS = 30

#: How many entries a tab shows at a time; Show older loads the next page.
PAGE_SIZE = 20

#: The three tabs, in the order they are drawn, what each is called, and who it is for in a sentence.
TABS = ("members", "guild-staff", "admins")
TAB_LABELS = {"members": "For Members", "guild-staff": "For Guild Staff", "admins": "For Admins"}
TAB_NOUNS = {"members": "members", "guild-staff": "guild staff", "admins": "admins"}

#: What the chip on an entry says for a finer audience key. The three tabs get no chip.
AUDIENCE_LABELS = {
    "guild-leads": "Guild Leads",
    "orientors": "Orientors",
    "treasurers": "Treasurers",
    "class-reviewers": "Class Reviewers",
    "equipment-managers": "Equipment Managers",
    "instructors": "Instructors",
}


def history_slug(date: str, title: str) -> str:
    """The slug of a frozen ``history.json`` entry, which the sweep left without one.

    Twelve hex characters of a hash over the date and the title: short enough to keep the seen
    column small, and stable across machines, unlike a title that someone later edits in place.
    """
    return hashlib.sha256(f"{date}|{title}".encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Picture:
    """One shot as the stepper draws it (#1050): its step number, caption, frame and where the file is."""

    number: int
    caption: str
    frame: str
    url: str


@dataclass(frozen=True)
class Entry:
    """One changelog entry as What's New reads it."""

    slug: str
    date: str
    title: str
    changes: tuple[str, ...]
    audiences: frozenset[str]
    feature: str
    guidance: str
    #: The captioned screenshots a ``shots`` or ``tour`` fragment walks through (#1050); a frozen entry from before them has none.
    shots: tuple[Shot, ...] = ()

    @classmethod
    def from_changelog(cls, raw: dict[str, Any]) -> Entry:
        """An entry from ``CHANGELOG``, with the defaults a frozen entry from before #1007 needs.

        A ``history.json`` row has no ``slug`` (the sweep drops it) and, from before the keys,
        no ``audiences``, ``feature`` or ``guidance``: it is for members and opens nothing. One
        from before #1050 has no ``shots``; one written since carries each shot's caption and frame.
        """
        return cls(
            slug=raw.get("slug") or history_slug(raw["date"], raw["title"]),
            date=raw["date"],
            title=raw["title"],
            changes=tuple(raw["changes"]),
            audiences=frozenset(raw.get("audiences") or ("members",)),
            feature=raw.get("feature", ""),
            guidance=raw.get("guidance", "none"),
            shots=tuple(Shot(caption=shot["caption"], frame=shot["frame"]) for shot in raw.get("shots", ())),
        )

    def on_tab(self, tab: str) -> bool:
        """Whether this entry lists under ``tab``: any of its audiences rolls up to it."""
        return any(AUDIENCES[key] == tab for key in self.audiences)

    def under(self, feature: str) -> bool:
        """Whether this entry belongs to ``feature`` or a key under it (``guild`` covers ``guild.*``)."""
        return covers(feature, self.feature)

    def chips(self, tab: str) -> list[str]:
        """The finer audiences of this entry that roll up to ``tab``, as chip labels, in label order."""
        return sorted(
            AUDIENCE_LABELS[key] for key in self.audiences if key in AUDIENCE_LABELS and AUDIENCES[key] == tab
        )

    @property
    def steps(self) -> tuple[str, ...]:
        """The registry's How it works lines, shown at level shots or tour when the entry has no shots of its own.

        An entry with shots lists their captions instead, so the lines only reach a frozen
        entry from before #1050 whose level promised teaching.
        """
        if self.guidance not in SHOT_LEVELS or self.feature not in GUIDANCE:
            return ()
        return GUIDANCE[self.feature].steps

    @property
    def pictures(self) -> list[Picture]:
        """The entry's shots with their URLs, in step order (#1050).

        Each file is ``guidance/<slug>/step-N.png`` on the default storage, so production
        reads the R2 public host and a dev box its media folder; the key comes from
        :func:`plfog.changelog.shot_key`, the same one the upload script writes and the
        ``fragment`` check HEADs. No query: the storage builds the URL from its settings.
        """
        from django.core.files.storage import default_storage

        return [
            Picture(
                number=number,
                caption=shot.caption,
                frame=shot.frame,
                url=default_storage.url(shot_key(self.slug, number)),
            )
            for number, shot in enumerate(self.shots, start=1)
        ]


@dataclass(frozen=True)
class Row:
    """An entry as the modal body draws it on one tab: its chips and the links the viewer may follow."""

    entry: Entry
    chips: list[str]
    open_url: str
    tour_url: str


@dataclass(frozen=True)
class DateGroup:
    """The rows of one date, newest date first in the body."""

    date: str
    rows: list[Row]


@dataclass(frozen=True)
class Page:
    """One page of a tab: its date groups, whether its first date continues the page before, and what follows."""

    groups: list[DateGroup]
    #: True when the first group's date is the date the previous page ended on, so its eyebrow is not drawn again.
    joins_previous: bool
    #: The slug Show older asks after, or ``""`` on the last page.
    older_after: str

    @property
    def slugs(self) -> list[str]:
        """Every entry on this page, for the seen write."""
        return [row.entry.slug for group in self.groups for row in group.rows]


def window_start(today: dt.date | None = None) -> dt.date:
    """The first day inside the window: an entry dated on or after it is new."""
    return (today or timezone.localdate()) - dt.timedelta(days=NEW_DAYS)


def all_entries() -> list[Entry]:
    """Every entry, newest first, read from the changelog as it stands now."""
    from plfog import version

    return [Entry.from_changelog(raw) for raw in version.CHANGELOG]


def window_entries() -> list[Entry]:
    """Every entry inside the window, newest first."""
    start = window_start().isoformat()
    return [entry for entry in all_entries() if entry.date >= start]


def can_open(entry: Entry, audiences: frozenset[str]) -> bool:
    """Whether a viewer with ``audiences`` is in the entry's audiences: Open it shows, and the entry counts as unseen."""
    return bool(entry.audiences & audiences)


def open_url(entry: Entry, member: Member) -> str:
    """Where the entry's feature lives for ``member``, or ``""`` when there is nothing to open.

    Empty for an entry with no feature, and for a member the feature's page cannot be resolved
    for (a guild tab for a member in no guild raises ``ValueError``): Open it is hidden, never
    broken. :meth:`WhatsNew._open_url` is the same answer with the guild resolved once per page.
    """
    if not entry.feature:
        return ""
    try:
        return url_for(entry.feature, member)
    except ValueError:
        return ""


def tour_url(entry: Entry, member: Member) -> str:
    """The Take the tour link for a level ``tour`` entry, or ``""`` when the member cannot take it.

    The tour's own ``audience`` decides, as the Help page does; a member it admits always has the
    guild its entry page needs, so the URL resolves.
    """
    from core.tours import TOURS, entry_url_for

    guidance = GUIDANCE.get(entry.feature)
    if entry.guidance != "tour" or guidance is None or guidance.tour is None:
        return ""
    tour = TOURS[guidance.tour]
    return entry_url_for(tour, member) if tour.audience(member) else ""


def record(member: Member, *, seen: Iterable[str] = (), opened: Iterable[str] = ()) -> bool:
    """Add ``seen`` and ``opened`` to the member's two lists in one write, and say whether anything was written.

    ``seen`` is what the member has looked at (``Member.whats_new_seen``, the badges' state);
    ``opened`` is what What's New had on offer at an open (``Member.whats_new_opened``, the
    buttons' state: the whole window from Home or a feature's entries from its button, listed on
    the page shown or not, so one open quiets the button until a release adds an entry). Each
    stored list is the union, kept to slugs still inside the window (D6), so it stays at about
    the size of one month's releases. No write when neither changes, one UPDATE when either does:
    a Member save costs a lookup as well, so the modal's two writes travel together.
    """
    keep = {entry.slug for entry in window_entries()}
    changed: list[str] = []
    for field, slugs in (("whats_new_seen", seen), ("whats_new_opened", opened)):
        stored = set(getattr(member, field) or [])  # a NULL from an older row reads as nothing
        merged = (stored | set(slugs)) & keep
        if merged != stored:
            setattr(member, field, sorted(merged))
            changed.append(field)
    if not changed:
        return False
    member.save(update_fields=changed)
    return True


def mark_seen(member: Member, slugs: Iterable[str]) -> bool:
    """Record ``slugs`` as looked at: the badges' state."""
    return record(member, seen=slugs)


def mark_opened(member: Member, slugs: Iterable[str]) -> bool:
    """Record ``slugs`` as on offer at an open of What's New: the buttons' state."""
    return record(member, opened=slugs)


class WhatsNew:
    """What's New for one request: the viewer's audiences, their entries and their seen state.

    Everything expensive is a ``cached_property``, so a page that renders the Home button pays
    the roles query once and a page that never reads it pays nothing.
    """

    def __init__(self, request: HttpRequest) -> None:
        self.request = request
        self.member: Member | None = getattr(request.user, "member", None) if request.user.is_authenticated else None
        self._urls: dict[str, str] = {}
        self._kwargs: dict[Callable[[Member], dict[str, Any]], dict[str, Any] | None] = {}

    @cached_property
    def entries(self) -> list[Entry]:
        """Every entry, newest first."""
        return all_entries()

    @cached_property
    def window(self) -> list[Entry]:
        """The entries that can be unseen: those inside the last :data:`NEW_DAYS` days."""
        start = window_start().isoformat()
        return [entry for entry in self.entries if entry.date >= start]

    @cached_property
    def audiences(self) -> frozenset[str]:
        """The audience keys this viewer is in, by the effective role (D5).

        ``members`` is :func:`hub.spotlight.sees_spotlight` for a viewer the preview lets act as
        a member (an admin previewing as a Guest is nobody, as the Door Code has it); the rest
        follow from it. ``guild-staff`` is a guild officer, or a member who leads or staffs a
        guild; ``admins`` is the admin role, or a member holding any admin capability, which a
        preview drops the way :func:`membership.permissions._capability_counts` does. The finer
        keys read the member's own rows, in the one query :meth:`_flags` makes.
        """
        from hub.spotlight import sees_spotlight

        view_as = getattr(self.request, "view_as", None)
        member = self.member
        if view_as is None or member is None or not view_as.is_member or not sees_spotlight(member):
            return frozenset()
        flags = self._flags(member)
        held = {"members"}
        if member.is_instructor:
            held.add("instructors")
        if view_as.is_guild_officer or flags["leads"] or flags["staffs"]:
            held.add("guild-staff")
        if flags["leads"] or flags["co_leads"]:
            held.add("guild-leads")
        if flags["treasurer"]:
            held.add("treasurers")
        if flags["orienter"]:
            held.add("orientors")
        if flags["leads"] or flags["reviews"]:
            held.add("class-reviewers")
        if flags["equipment"]:
            held.add("equipment-managers")
        if view_as.is_admin or (flags["capability"] and not view_as.is_previewing):
            held.add("admins")
        return frozenset(held)

    @staticmethod
    def _flags(member: Member) -> Mapping[str, object]:
        """The member's leadership rows as eight booleans, in ONE query of correlated EXISTS."""
        from django.db.models import Exists, OuterRef

        from membership.models import AdminCapability, EquipmentStaffMembership, Guild, GuildStaffMembership, Member

        staff = GuildStaffMembership.objects.filter(member=OuterRef("pk"))
        role = GuildStaffMembership.Role
        flags: Mapping[str, object] = (
            Member.objects.filter(pk=member.pk)
            .annotate(
                leads=Exists(Guild.objects.filter(guild_lead=OuterRef("pk"))),
                staffs=Exists(staff),
                co_leads=Exists(staff.filter(role=role.CO_LEAD)),
                treasurer=Exists(staff.filter(role=role.TREASURER)),
                orienter=Exists(staff.filter(role=role.ORIENTER)),
                reviews=Exists(staff.filter(can_review_classes=True)),
                equipment=Exists(EquipmentStaffMembership.objects.filter(member=OuterRef("pk"))),
                capability=Exists(AdminCapability.objects.filter(member=OuterRef("pk"))),
            )
            .values("leads", "staffs", "co_leads", "treasurer", "orienter", "reviews", "equipment", "capability")
            .get()
        )
        return flags

    def entries_for(self, *, tab: str | None = None, feature: str | None = None) -> list[Entry]:
        """Every entry on ``tab`` (every tab when None) and under ``feature`` (every feature when None), newest first."""
        return [
            entry
            for entry in self.entries
            if (tab is None or entry.on_tab(tab)) and (feature is None or entry.under(feature))
        ]

    def page_for(self, tab: str, feature: str | None = None, after: str = "") -> Page:
        """One page of the tab as the body draws it: :data:`PAGE_SIZE` entries after ``after`` (the top when empty).

        Raises ``LookupError`` when ``after`` names no entry on the tab, which happens when the
        changelog changed under an open modal.
        """
        listed = self.entries_for(tab=tab, feature=feature)
        start, previous_date = 0, ""
        if after:
            index = next((i for i, entry in enumerate(listed) if entry.slug == after), None)
            if index is None:
                raise LookupError(f"No entry {after!r} on the {tab} tab")
            start, previous_date = index + 1, listed[index].date
        chunk = listed[start : start + PAGE_SIZE]
        return Page(
            groups=[
                DateGroup(date=date, rows=[self._row(entry, tab) for entry in entries])
                for date, entries in groupby(chunk, key=lambda entry: entry.date)
            ],
            joins_previous=bool(chunk) and chunk[0].date == previous_date,
            older_after=chunk[-1].slug if len(listed) > start + PAGE_SIZE else "",
        )

    def groups_for(self, tab: str, feature: str | None = None) -> list[DateGroup]:
        """The tab's first page, grouped by date, newest first, with the viewer's links."""
        return self.page_for(tab, feature).groups

    def _row(self, entry: Entry, tab: str) -> Row:
        opens = self.member is not None and can_open(entry, self.audiences)
        return Row(
            entry=entry,
            chips=entry.chips(tab),
            open_url=self._open_url(entry) if opens else "",
            tour_url=tour_url(entry, self.member) if opens and self.member is not None else "",
        )

    def _open_url(self, entry: Entry) -> str:
        """:func:`open_url` with each feature resolved once, and each resolver (the member's guild) run once.

        Six ``guild.*`` entries share ``_guild_slug``, so the guild is looked up once for the
        page, not once per entry.
        """
        assert self.member is not None
        if not entry.feature:
            return ""
        if entry.feature not in self._urls:
            guidance = GUIDANCE[entry.feature]
            resolver = guidance.url_kwargs
            if resolver is None:
                self._urls[entry.feature] = guidance.url({})
            else:
                if resolver not in self._kwargs:
                    try:
                        self._kwargs[resolver] = resolver(self.member)
                    except ValueError:
                        self._kwargs[resolver] = None
                kwargs = self._kwargs[resolver]
                self._urls[entry.feature] = guidance.url(kwargs) if kwargs is not None else ""
        return self._urls[entry.feature]

    def is_unseen(self, entry: Entry) -> bool:
        """Whether the viewer has an unseen entry here: inside the window, not yet looked at, and in their audiences.

        The window and the seen list are asked first, since they cost nothing: a member who has
        looked at everything in the window pays no roles query on a page whose only readers are badges.
        """
        return (
            self.member is not None
            and entry.date >= window_start().isoformat()
            and entry.slug not in (self.member.whats_new_seen or [])
            and can_open(entry, self.audiences)
        )

    def has_unseen(self, feature: str) -> bool:
        """Whether the viewer has an unseen entry under ``feature``: its badge shows (part 3)."""
        return any(self.is_unseen(entry) for entry in self.window if entry.under(feature))

    def is_unopened(self, entry: Entry) -> bool:
        """Whether What's New has not been opened over this entry: inside the window, not in the opened list, in their audiences."""
        return (
            self.member is not None
            and entry.date >= window_start().isoformat()
            and entry.slug not in (self.member.whats_new_opened or [])
            and can_open(entry, self.audiences)
        )

    @cached_property
    def unopened(self) -> bool:
        """Home's button: its dot and pulse, until the viewer opens What's New or a release adds an entry."""
        return any(self.is_unopened(entry) for entry in self.window)

    def has_unopened(self, feature: str) -> bool:
        """A feature's button: its dot and pulse, while an entry under ``feature`` is one no open has covered."""
        return any(self.is_unopened(entry) for entry in self.window if entry.under(feature))

    @staticmethod
    def has_entries(feature: str) -> bool:
        """Whether any entry, of any age, belongs to ``feature`` or a key under it: its button renders at all.

        The whole changelog, not the window, so a feature's button stays where members learned
        to find it once a release has named the feature; opened after the window it says so.
        """
        from plfog import version

        return any(covers(feature, raw.get("feature", "")) for raw in version.CHANGELOG)

    def default_tab(self, feature: str | None = None) -> str:
        """The tab the modal opens on: the first with something unseen for the viewer, else For Members.

        Opened on a feature (its button), only that feature's entries count, and with nothing
        unseen the first tab with any entry of the feature's, so the button never lands on an
        empty tab beside a tab that has what it promised (#1030).
        """
        for tab in TABS:
            if any(
                self.is_unseen(entry)
                for entry in self.window
                if entry.on_tab(tab) and (feature is None or entry.under(feature))
            ):
                return tab
        if feature is not None:
            for tab in TABS:
                if any(entry.on_tab(tab) for entry in self.entries if entry.under(feature)):
                    return tab
        return TABS[0]

    def record(self, *, seen: Iterable[str] = (), opened: Iterable[str] = ()) -> bool:
        """Record what the viewer looked at and what this open had on offer, in one write; nothing for an account with no member."""
        return self.member is not None and record(self.member, seen=seen, opened=opened)

    def mark_seen(self, slugs: Iterable[str]) -> bool:
        """Record ``slugs`` as seen for the viewer; nothing for an account with no member."""
        return self.record(seen=slugs)


def whats_new_for(request: HttpRequest) -> WhatsNew:
    """The request's one :class:`WhatsNew`, built on first use and kept on the request."""
    found: WhatsNew | None = getattr(request, "_whats_new", None)
    if found is None:
        found = WhatsNew(request)
        request._whats_new = found  # type: ignore[attr-defined]
    return found


def audiences_for(request: HttpRequest) -> frozenset[str]:
    """The audience keys the request's viewer is in (D5)."""
    return whats_new_for(request).audiences


def entries_for(request: HttpRequest, *, tab: str | None = None, feature: str | None = None) -> list[Entry]:
    """Every entry for the request's viewer, by tab and by feature prefix, newest first."""
    return whats_new_for(request).entries_for(tab=tab, feature=feature)


def unopened(request: HttpRequest) -> bool:
    """Whether the request's viewer has an entry in their audiences no open of What's New has covered: Home's dot."""
    return whats_new_for(request).unopened
