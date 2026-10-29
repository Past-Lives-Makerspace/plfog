"""What's New for one viewer (#1007, part 2): which entries, on which tab, and what they have seen.

The entries are ``plfog.version.CHANGELOG`` as it stands (D1: fragments and ``history.json``,
no model), cut to the last :data:`NEW_DAYS` days. Each one names its ``audiences`` (D2;
a frozen entry from before the keys is for ``members``), and a tab lists every entry whose
audience rolls up to it (:data:`plfog.changelog.AUDIENCES`), to every member, as the changelog
modal shows everything to everyone (D8). Who the viewer is comes from ``request.view_as`` (D5),
in one query however many roles there are to ask about, and decides only two things: whether
Open it shows on an entry and whether the entry counts as unseen.

Seen state is ``Member.whats_new_seen``, a list of entry slugs pruned to the window on every
write (D6). One :class:`WhatsNew` per request, built on first use by :func:`whats_new_for`, so
the Home button's dot, the modal and part 3's badges share the roles query (D9).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from itertools import groupby
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from core.guidance import GUIDANCE, covers, url_for
from plfog.changelog import AUDIENCES

if TYPE_CHECKING:
    from django.http import HttpRequest

    from membership.models import Member

#: How long an entry counts as new: listed in What's New, unseen until looked at, badged (D6).
NEW_DAYS = 30

#: The three tabs, in the order they are drawn, and what each is called.
TABS = ("members", "guild-staff", "admins")
TAB_LABELS = {"members": "For Members", "guild-staff": "For Guild Staff", "admins": "For Admins"}

#: What the chip on an entry says for a finer audience key. The three tabs get no chip.
AUDIENCE_LABELS = {
    "guild-leads": "Guild Leads",
    "orientors": "Orientors",
    "treasurers": "Treasurers",
    "class-reviewers": "Class Reviewers",
    "equipment-managers": "Equipment Managers",
    "instructors": "Instructors",
}


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

    @classmethod
    def from_changelog(cls, raw: dict[str, Any]) -> Entry:
        """An entry from ``CHANGELOG``, with the defaults a frozen entry from before #1007 needs.

        A ``history.json`` row has no ``slug`` (the sweep drops it), no ``audiences``, no
        ``feature`` and no ``guidance``: it is for members, opens nothing, and is identified by
        its date and title, which is also how the changelog modal tells history rows apart.
        """
        slug = raw.get("slug") or f"{raw['date']}-{raw['title']}"
        return cls(
            slug=slug,
            date=raw["date"],
            title=raw["title"],
            changes=tuple(raw["changes"]),
            audiences=frozenset(raw.get("audiences") or ("members",)),
            feature=raw.get("feature", ""),
            guidance=raw.get("guidance", "none"),
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
        """The registry's How it works lines, shown at level clip or tour (the clip itself is #1008)."""
        if self.guidance not in ("clip", "tour") or self.feature not in GUIDANCE:
            return ()
        return GUIDANCE[self.feature].steps


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


def window_start(today: dt.date | None = None) -> dt.date:
    """The first day inside the window: an entry dated on or after it is new."""
    return (today or timezone.localdate()) - dt.timedelta(days=NEW_DAYS)


def window_entries() -> list[Entry]:
    """Every entry inside the window, newest first, read from the changelog as it stands now."""
    from plfog import version

    start = window_start().isoformat()
    return [Entry.from_changelog(raw) for raw in version.CHANGELOG if raw["date"] >= start]


def can_open(entry: Entry, audiences: frozenset[str]) -> bool:
    """Whether a viewer with ``audiences`` is in the entry's audiences: Open it shows, and the entry counts as unseen."""
    return bool(entry.audiences & audiences)


def open_url(entry: Entry, member: Member) -> str:
    """Where the entry's feature lives for ``member``, or ``""`` when there is nothing to open.

    Empty for an entry with no feature, and for a member the feature's page cannot be resolved
    for (a guild tab for a member in no guild raises ``ValueError``): Open it is hidden, never
    broken.
    """
    if not entry.feature:
        return ""
    try:
        return url_for(entry.feature, member)
    except ValueError:
        return ""


def tour_url(entry: Entry, member: Member) -> str:
    """The Take the tour link for a level ``tour`` entry, or ``""`` when the member cannot take it."""
    from core.tours import TOURS, entry_url_for

    guidance = GUIDANCE.get(entry.feature)
    if entry.guidance != "tour" or guidance is None or guidance.tour is None:
        return ""
    tour = TOURS[guidance.tour]
    if not tour.audience(member):
        return ""
    try:
        return entry_url_for(tour, member)
    except ValueError:
        return ""


def mark_seen(member: Member, slugs: list[str]) -> bool:
    """Record ``slugs`` as seen, pruning to the window, and say whether anything was written.

    The stored list is the union, kept to slugs still inside the window (D6), so it stays at
    about the size of one month's releases. No write when the result is what is stored already.
    """
    keep = {entry.slug for entry in window_entries()}
    seen = (set(member.whats_new_seen) | set(slugs)) & keep
    if seen == set(member.whats_new_seen):
        return False
    member.whats_new_seen = sorted(seen)
    member.save(update_fields=["whats_new_seen"])
    return True


class WhatsNew:
    """What's New for one request: the viewer's audiences, their entries and their seen state.

    Everything expensive is a ``cached_property``, so a page that renders the Home button pays
    the roles query once and a page that never reads it pays nothing.
    """

    def __init__(self, request: HttpRequest) -> None:
        self.request = request
        self.member: Member | None = getattr(request.user, "member", None) if request.user.is_authenticated else None
        self._urls: dict[str, str] = {}

    @cached_property
    def entries(self) -> list[Entry]:
        return window_entries()

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
        """The window's entries on ``tab`` (every tab when None) and under ``feature`` (every feature when None)."""
        return [
            entry
            for entry in self.entries
            if (tab is None or entry.on_tab(tab)) and (feature is None or entry.under(feature))
        ]

    def groups_for(self, tab: str, feature: str | None = None) -> list[DateGroup]:
        """The tab's entries as the body draws them: grouped by date, newest first, with the viewer's links."""
        return [
            DateGroup(date=date, rows=[self._row(entry, tab) for entry in entries])
            for date, entries in groupby(self.entries_for(tab=tab, feature=feature), key=lambda entry: entry.date)
        ]

    def _row(self, entry: Entry, tab: str) -> Row:
        opens = self.member is not None and can_open(entry, self.audiences)
        return Row(
            entry=entry,
            chips=entry.chips(tab),
            open_url=self._open_url(entry) if opens else "",
            tour_url=tour_url(entry, self.member) if opens and self.member is not None else "",
        )

    def _open_url(self, entry: Entry) -> str:
        """One resolve per feature key however many entries share it: a guild tab's URL looks the guild up."""
        assert self.member is not None
        if entry.feature not in self._urls:
            self._urls[entry.feature] = open_url(entry, self.member)
        return self._urls[entry.feature]

    def is_unseen(self, entry: Entry) -> bool:
        """Whether the viewer has an unseen entry here: not yet looked at, and in their audiences.

        The seen list is asked first, since it costs nothing: a member who has looked at
        everything in the window pays no roles query on a page whose only readers are badges.
        """
        return (
            self.member is not None and entry.slug not in self.member.whats_new_seen and can_open(entry, self.audiences)
        )

    @cached_property
    def unseen(self) -> bool:
        """The Home button's dot."""
        return any(self.is_unseen(entry) for entry in self.entries)

    def has_unseen(self, feature: str) -> bool:
        """Whether the viewer has an unseen entry under ``feature``: its badge shows and its button pulses (part 3)."""
        return any(self.is_unseen(entry) for entry in self.entries_for(feature=feature))

    @staticmethod
    def has_entries(feature: str) -> bool:
        """Whether any entry, of any age, belongs to ``feature`` or a key under it: its button renders at all.

        The whole changelog, not the window, so a feature's button stays where members learned
        to find it once a release has named the feature; opened after the window it says so.
        """
        from plfog import version

        return any(covers(feature, raw.get("feature", "")) for raw in version.CHANGELOG)

    def default_tab(self) -> str:
        """The tab the modal opens on: the first with something unseen for the viewer, else For Members."""
        for tab in TABS:
            if any(self.is_unseen(entry) for entry in self.entries_for(tab=tab)):
                return tab
        return TABS[0]

    def mark_seen(self, slugs: list[str]) -> bool:
        """Record ``slugs`` as seen for the viewer; nothing for an account with no member."""
        return self.member is not None and mark_seen(self.member, slugs)


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
    """The window's entries for the request's viewer, by tab and by feature prefix."""
    return whats_new_for(request).entries_for(tab=tab, feature=feature)


def unseen(request: HttpRequest) -> bool:
    """Whether the request's viewer has an unseen entry in their audiences."""
    return whats_new_for(request).unseen
