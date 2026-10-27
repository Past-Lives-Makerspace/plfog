"""Member Home / Dashboard aggregation service.

Thin orchestration only — the querying lives on ``membership`` managers/model methods
(``Member.joined_guilds``, ``Member.profile_completeness``, ``CommunityEvent.for_member``,
``GuildAnnouncement.for_member``, ``OrientationBooking.upcoming``, ``CalendarEvent.upcoming``),
per fat-models/skinny-views and the hub-app rule ("no models — reads from ``membership``").
This module just fetches those, normalizes the "Your upcoming" sources into one shape,
sorts soonest-first, and caps each section. Mirrors how ``hub/calendar_entries.py`` (build
synthetic entries) and ``hub/calendar_service.py`` (orchestration) split query vs. assembly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime as datetime_type
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.urls import reverse
from django.utils import timezone

from membership.models import CalendarEvent, CommunityEvent, GuildAnnouncement
from polls.models import PollCard

if TYPE_CHECKING:
    from classes.models import ClassOffering, ClassSession
    from core.models import Announcement
    from membership.models import Member

# How far out "Your upcoming" looks, and how many rows each section shows.
UPCOMING_HORIZON_DAYS = 90
UPCOMING_CAP = 5
ANNOUNCEMENTS_CAP = 4
# The Teaching card shows at most this many rows (bounced first, then drafts, in review, this week).
TEACHING_CAP = 3
TEACHING_WEEK_DAYS = 7


@dataclass(frozen=True)
class HomeBanner:
    """The Spotlight messages crawling across the top of Home (#889, a ticker since #935): the live ones in order, and their signature.

    The signature (:meth:`core.models.Announcement.signature`) is what a member's Close keeps in
    the browser; the ticker stays closed until it changes.
    """

    items: list[Announcement]
    signature: str

    @classmethod
    def load(cls, now: datetime_type) -> HomeBanner | None:
        """The ticker at ``now``, or None while nothing is live. One query."""
        from core.models import Announcement

        rows = list(Announcement.objects.live(now))
        return cls(rows, Announcement.signature(rows)) if rows else None


@dataclass(frozen=True)
class TeachingRow:
    """One row of the home page's Teaching card: a class, its state, and the one next step."""

    title: str
    note: str
    cta: str
    url: str


# The kind pill's words, the key its color and the date poster's color are named by (home-featured.css), and
# the noun its View button names. Read with ``[...]``, so a new kind with no entry fails loudly.
_KIND_KEYS = {
    "Class": "class",
    "Event": "event",
    "Meeting": "meeting",
    "Studio hours": "studio-hours",
    "Orientation": "orientation",
}
# An orientation has no page of its own: its item names what its View opens (``opens``), so it has no entry here.
_VIEW_NOUNS = {
    "Class": "Class",
    "Event": "Event",
    "Meeting": "Meeting",
    "Studio hours": "Studio Hours",
}


@dataclass(frozen=True)
class UpcomingItem:
    """One item of Home's Upcoming queue (#937): a row of the list, and the featured card when it is first.

    Every source (orientation bookings, community events, guild meetings, studio hours and class
    sessions) comes down to this, so the template renders them as one soonest first queue.
    ``kind`` is the pill's words ("Orientation", "Class", "Meeting", "Studio hours", "Event").
    ``photo_url`` is the picture the featured card shows, ``""`` for none, when the card draws
    the date poster instead; ``photo_position`` is its crop. ``flyer_url`` is the printable
    flyer, which only community events have. ``instructors`` names who teaches a class. ``opens``
    names the page View opens when it is not the item's own ("Guild" or "Equipment" for an
    orientation booking, which opens its owner's page).
    """

    title: str
    start: datetime_type
    end: datetime_type
    kind: str
    url: str
    location: str = ""
    guild_name: str = ""
    video_url: str = ""
    photo_url: str = ""
    photo_position: str = "50% 50%"
    flyer_url: str = ""
    instructors: str = ""
    opens: str = ""

    @property
    def kind_key(self) -> str:
        """The kind as a CSS name ("studio-hours"), for the pill's and the poster's color."""
        return _KIND_KEYS[self.kind]

    @property
    def view_label(self) -> str:
        """The featured card's View button, in Title Case, naming what it opens: "View Class", "View Guild"."""
        return f"View {self.opens or _VIEW_NOUNS[self.kind]}"


@dataclass(frozen=True)
class GuildShortcut:
    """A guild the member has joined, flagged when they also lead or staff it."""

    guild: Any
    is_staff: bool = False


def build_home_context(member: Member) -> dict[str, Any]:
    """Assemble the Member Home context for a linked member.

    Returns the home blocks — ``upcoming`` (capped, soonest-first), ``announcements``
    (active guild announcements from joined guilds), ``my_guilds`` (joined, lead/staff
    flagged), ``my_cubbies`` (the cubbies they rent, #978), and the ``onboarding`` "Get started" checklist with its ``show_onboarding``
    gate (built ONCE here, not re-derived per property in the template), and ``home_poll``,
    the open poll's card (#925), None while no poll is open. All business logic lives on
    ``membership`` and ``polls`` managers/props; this only fetches, normalizes, sorts, and caps.
    """
    return {
        "upcoming": _upcoming_items(member),
        "announcements": _announcements(member),
        "my_guilds": _my_guilds(member),
        "my_cubbies": _my_cubbies(member),
        "onboarding": member.onboarding,
        "show_onboarding": member.show_onboarding,
        "teaching": _teaching(member),
        "overdue_reviews": _overdue_reviews(member),
        "home_poll": PollCard.open_for(member, timezone.now()),
    }


def _overdue_reviews(member: Member) -> list[Any]:
    """Class reviews waiting 3 days or more (#817), for the people who can review them.

    Admins and CMS Administrators only; everyone else gets an empty list, so the card is
    omitted. The same list as the Discord alert and the admin queue marker.
    """
    from membership.models import AdminCapability

    if not (member.is_fog_admin or member.has_admin_capability(AdminCapability.Capability.CLASS_APPROVER)):
        return []
    from classes.overdue import overdue_reviews

    return overdue_reviews()


def _teaching(member: Member) -> list[TeachingRow]:
    """Rows for the Teaching card: what needs the instructor, in priority order, capped.

    Empty (so the card is omitted) for a member without teaching access or with nothing
    to show. Order: changes requested ("Fix and resubmit"), drafts ("Finish"), in review
    (the stage), then this week's sessions (the session time). Lists are annotated and
    prefetched so no row costs a query of its own.
    """
    if not member.can_create_classes:
        return []
    from classes.models import ClassOffering

    mine = (
        ClassOffering.objects.for_instructor(member)
        .with_lifecycle_inputs()
        .select_related("category__guild")
        .prefetch_related("approvals")
    )
    rows: list[TeachingRow] = []
    drafts = mine.filter(status=ClassOffering.Status.DRAFT)
    bounced = drafts.filter(bounced=True).order_by("-updated_at")  # type: ignore[misc]  # django-stubs can't see annotate() aliases
    plain = drafts.filter(bounced=False).order_by("-updated_at")  # type: ignore[misc]  # django-stubs can't see annotate() aliases
    for offering in bounced[:TEACHING_CAP]:
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        rows.append(TeachingRow(offering.title, offering.lifecycle_note, "Fix and resubmit", url))
    for offering in plain[:TEACHING_CAP]:
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        rows.append(TeachingRow(offering.title, "Draft", "Finish", url))
    for offering in mine.filter(status=ClassOffering.Status.PENDING).order_by("created_at")[:TEACHING_CAP]:
        url = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        rows.append(TeachingRow(offering.title, offering.lifecycle_label, "Open", url))
    now = timezone.now()
    week_end = now + timedelta(days=TEACHING_WEEK_DAYS)
    from classes.models import ClassSession

    this_week = (
        ClassSession.objects.filter(
            class_offering__instructors=member,
            class_offering__status=ClassOffering.Status.PUBLISHED,
            starts_at__gte=now,
            starts_at__lt=week_end,
        )
        .select_related("class_offering")
        .order_by("starts_at")[:TEACHING_CAP]
    )
    for session in this_week:
        url = reverse("classes:teach_class_registrations", kwargs={"pk": session.class_offering_id})
        when = timezone.localtime(session.starts_at).strftime("%a %b %-d, %-I:%M %p")
        rows.append(TeachingRow(session.class_offering.title, when, "Roster", url))
    return rows[:TEACHING_CAP]


def _upcoming_items(member: Member) -> list[UpcomingItem]:
    """Home's Upcoming queue (#937): the member's next few horizon items, merged soonest first and capped.

    Merges the member's own upcoming orientation bookings, the next occurrence of each
    community/guild event visible to them (site-wide + their guilds), and the class
    sessions already materialized onto the calendar (``source="classes"``). The first is the
    featured card, the rest the list, so each item also carries what the card shows: its
    picture, its flyer and a class's instructors. A fixed number of queries however many
    items there are: one per source, and two more for the classes' sessions behind their rows.
    """
    now = timezone.now()
    today = timezone.localdate()
    horizon = today + timedelta(days=UPCOMING_HORIZON_DAYS)
    calendar_url = reverse("hub_community_calendar")

    items: list[UpcomingItem] = []

    # 1. The member's own orientation bookings — clearly personal. Owner-aware:
    # an equipment-owned booking has guild=None; the type resolves either owner.
    upcoming_bookings = member.orientation_bookings.upcoming().select_related(
        "slot", "orientation_type__guild", "orientation_type__equipment"
    )
    for booking in upcoming_bookings:
        orientation_type = booking.orientation_type
        owner_name = orientation_type.owner_name
        items.append(
            UpcomingItem(
                title=f"{owner_name} orientation",
                start=booking.slot.starts_at,
                end=booking.slot.ends_at,
                kind="Orientation",
                url=orientation_type.owner_page_path(),
                location=booking.slot.location,
                guild_name=owner_name,
                photo_url=orientation_type.photo.url if orientation_type.photo else "",
                opens="Equipment" if orientation_type.is_equipment_owned else "Guild",
            )
        )

    # 2. Published community/guild events visible to the member — the next in-window occurrence
    # each. Published only, like the Calendar: a proposal, a parked event or a draft (#831) is
    # not on the Calendar yet, so Home never shows it either.
    events = CommunityEvent.objects.published().upcoming().for_member(member).select_related("guild", "area")
    for event in events:
        occurrence = _next_occurrence(event, today, horizon, now)
        if occurrence is None:
            continue
        duration = event.ends_at - event.starts_at
        items.append(
            UpcomingItem(
                title=event.title,
                start=occurrence,
                end=occurrence + duration,
                kind=_event_kind(event),
                url=f"{reverse('hub_event_detail', args=[event.pk])}{event.date_query(occurrence)}",
                location=_event_place(event),
                guild_name=event.guild.name if event.guild_id else "",
                video_url=event.video_url,
                photo_url=event.hero_image_url,
                photo_position=event.hero_object_position,
                flyer_url=reverse("hub_event_flyer", args=[event.pk]),
            )
        )

    # 3. Classes at the space — already materialized as source="classes" calendar rows.
    # Only the soonest few can survive the final cap, so bound the fetch instead of pulling
    # every future class session.
    class_events = list(
        CalendarEvent.objects.upcoming().filter(source=CalendarEvent.Source.CLASSES).order_by("start_dt")[:UPCOMING_CAP]
    )
    sessions = _class_sessions(class_events)
    for event in class_events:
        session = sessions.get(_local_class_session_pk(event.uid))
        offering = session.class_offering if session is not None else None
        items.append(
            UpcomingItem(
                title=event.title,
                start=event.start_dt,
                end=event.end_dt,
                kind="Class",
                url=event.url or calendar_url,
                location=event.location,
                photo_url=offering.card_photo_url if offering is not None else "",
                photo_position=_class_photo_position(offering),
                instructors=offering.instructor_names if offering is not None else "",
            )
        )

    items.sort(key=lambda item: item.start)
    return items[:UPCOMING_CAP]


_LOCAL_CLASS_UID = "local-class-"


def _local_class_session_pk(uid: str) -> int | None:
    """The ``ClassSession`` pk behind a class calendar row's uid (``local-class-<pk>``), else None.

    ``hub.calendar_service.sync_local_class_events`` writes every class row with that uid; a row
    from anywhere else has no session, and its item simply has no picture.
    """
    rest = uid.removeprefix(_LOCAL_CLASS_UID)
    if rest == uid or not rest.isdigit():
        return None
    return int(rest)


def _class_sessions(rows: list[CalendarEvent]) -> dict[int | None, ClassSession]:
    """The sessions behind these class calendar rows, by pk, with each class, its category and instructors.

    Two queries (the sessions joined to their class and category, then every instructor between
    them), or none when no row names a session.
    """
    from classes.models import ClassSession

    pks = [pk for pk in (_local_class_session_pk(row.uid) for row in rows) if pk is not None]
    if not pks:
        return {}
    found = ClassSession.objects.filter(pk__in=pks).select_related("class_offering__category")
    return {session.pk: session for session in found.prefetch_related("class_offering__instructor_rows")}


def _class_photo_position(offering: ClassOffering | None) -> str:
    """A class picture's crop as the catalog card frames it; a category's photo sits at its centre."""
    if offering is not None and offering.hero_image_url:
        return offering.card_object_position
    return "50% 50%"


def _event_place(event: CommunityEvent) -> str:
    """Where an event is: its area and its location text, as much of the two as it has."""
    return " · ".join(part for part in (event.area.name if event.area is not None else "", event.location) if part)


def _event_kind(event: CommunityEvent) -> str:
    """The "Upcoming" widget label for a community event: standing studio hours, a guild
    meeting, or a site-wide event."""
    if event.event_type == CommunityEvent.EventType.STUDIO_HOURS:
        return "Studio hours"
    return "Meeting" if event.guild_id else "Event"


def _next_occurrence(event: CommunityEvent, frm: date_type, to: date_type, now: datetime_type) -> datetime_type | None:
    """The soonest still-future start of ``event`` within ``[frm, to]``, or ``None``.

    A non-recurring event yields its own start; a monthly series is expanded virtually via
    :meth:`CommunityEvent.occurrences_in`, and we take the first occurrence at/after ``now``.
    """
    for occurrence in event.occurrences_in(frm, to):
        if occurrence >= now:
            return occurrence
    return None


def _announcements(member: Member) -> list[GuildAnnouncement]:
    """Active guild announcements from the member's joined guilds, newest first, capped."""
    return list(
        GuildAnnouncement.objects.published().active().for_member(member).select_related("guild")[:ANNOUNCEMENTS_CAP]
    )


def _my_cubbies(member: Member) -> list[Any]:
    """The cubbies the member rents (#978), one Home line each: "Your Cubby: A3, Glaze Wall →"."""
    from membership.models import CubbyRental

    return list(
        CubbyRental.objects.live()
        .filter(member=member)
        .exclude(status=CubbyRental.Status.PENDING_PAYMENT)
        .select_related("cubby__unit__guild")
        .order_by("created_at", "pk")
    )


def _my_guilds(member: Member) -> list[GuildShortcut]:
    """The member's joined guilds, each flagged when they also lead or staff it."""
    staffed_ids = set(member.staffed_guilds.values_list("pk", flat=True))
    return [GuildShortcut(guild=guild, is_staff=guild.pk in staffed_ids) for guild in member.joined_guilds]
