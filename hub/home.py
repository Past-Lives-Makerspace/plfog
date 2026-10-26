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

import random
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime as datetime_type
from datetime import timedelta
from typing import TYPE_CHECKING, Any, TypeVar

from django.db.models import Model, QuerySet
from django.urls import reverse
from django.utils import timezone

from membership.models import CalendarEvent, CommunityEvent, GuildAnnouncement
from polls.models import PollCard, can_vote

if TYPE_CHECKING:
    from classes.models import ClassOffering
    from core.models import Announcement
    from membership.models import Member

_Row = TypeVar("_Row", bound=Model)

# Home's carousel (#888, #896): up to FEATURED_PER_KIND classes and as many events, the one kind
# topping the other up when it is short, never more than FEATURED_LIMIT slides in all.
FEATURED_LIMIT = 8
FEATURED_PER_KIND = 4
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


@dataclass(frozen=True)
class UpcomingItem:
    """One normalized row in the "Your upcoming" block.

    All four sources (orientation bookings, community/guild events, and class sessions)
    duck-type down to this so the template renders them in one uniform, soonest-first list.
    ``kind`` is the short badge label ("Orientation" / "Class" / "Meeting" / "Event").
    """

    title: str
    start: datetime_type
    end: datetime_type
    kind: str
    url: str
    location: str = ""
    guild_name: str = ""
    video_url: str = ""


@dataclass(frozen=True)
class FeaturedClass:
    """A class slide of the carousel at the top of Home's Upcoming card (#888, #896).

    ``photo_url`` is the catalog card's picture (``ClassOffering.card_photo_url``), ``""`` for
    a class with none; ``photo_position`` is its crop, as the catalog card frames it.
    """

    title: str
    url: str
    start: datetime_type
    instructors: str
    photo_url: str
    photo_position: str
    kind: str = "Class"


@dataclass(frozen=True)
class FeaturedEvent:
    """An event slide of the carousel at the top of Home's Upcoming card (#888, #896).

    ``event`` rides along for the flyer preview (``hub/partials/_event_flyer_sheet.html``),
    shown in place of a photo when the event has none; ``qr_svg`` is that sheet's QR, made
    only then. ``start`` is ``CommunityEvent.next_occurrence_start``, the date the flyer prints.
    """

    event: CommunityEvent
    title: str
    url: str
    flyer_url: str
    start: datetime_type
    place: str
    photo_url: str
    photo_position: str
    qr_svg: str
    kind: str = "Event"


@dataclass(frozen=True)
class GuildShortcut:
    """A guild the member has joined, flagged when they also lead or staff it."""

    guild: Any
    is_staff: bool = False


def build_home_context(member: Member) -> dict[str, Any]:
    """Assemble the Member Home context for a linked member.

    Returns the home blocks — ``upcoming`` (capped, soonest-first), ``announcements``
    (active guild announcements from joined guilds), ``my_guilds`` (joined, lead/staff
    flagged), and the ``onboarding`` "Get started" checklist with its ``show_onboarding``
    gate (built ONCE here, not re-derived per property in the template), and ``home_poll``,
    the open poll's card (#925), None while no poll is open, and ``can_vote_polls``, which gates
    the Past Polls link by the same rule as the page it opens. All business logic lives on
    ``membership`` and ``polls`` managers/props; this only fetches, normalizes, sorts, and caps.
    """
    return {
        "upcoming": _upcoming_items(member),
        "announcements": _announcements(member),
        "my_guilds": _my_guilds(member),
        "onboarding": member.onboarding,
        "show_onboarding": member.show_onboarding,
        "teaching": _teaching(member),
        "overdue_reviews": _overdue_reviews(member),
        "featured": _featured(member),
        "home_poll": PollCard.open_for(member, timezone.now()),
        "can_vote_polls": can_vote(member),
    }


def _random_order() -> str:
    """The ordering that shuffles the candidates in the database: ``"?"``, ``ORDER BY RANDOM()``.

    A seam of its own so a spec can order by ``"pk"`` instead and name which rows win.
    """
    return "?"


def _pick_some(candidates: QuerySet[_Row]) -> list[_Row]:
    """Up to :data:`FEATURED_LIMIT` distinct rows of ``candidates``, shuffled by the database.

    Picked again on every call, so each load of Home rotates through a different set (#896);
    one query, and no row past the limit is loaded.
    """
    return list(candidates.order_by(_random_order())[:FEATURED_LIMIT])


def _featured(member: Member) -> list[FeaturedClass | FeaturedEvent]:
    """The carousel's slides: classes and events at random, mixed (#896).

    Up to :data:`FEATURED_PER_KIND` of each; when one kind has fewer, the other fills the
    carousel up to :data:`FEATURED_LIMIT`. The two kinds are shuffled together, so a class and
    an event take turns at random. Classes only while the Classes switch is on, as the Classes
    quick link above. Three queries for the picks (the classes, their instructors, the events)
    plus the switch and the settings row the catalog's demo gate reads.
    """
    from classes.models import ClassOffering
    from core.features import is_on

    classes: list[ClassOffering] = []
    if is_on("catalog"):
        classes = _pick_some(ClassOffering.objects.featurable().prefetch_related("instructor_rows"))
    events = _pick_some(CommunityEvent.objects.featurable_for(member))
    class_room = max(FEATURED_PER_KIND, FEATURED_LIMIT - len(events))
    kept_classes = classes[:class_room]
    kept_events = events[: FEATURED_LIMIT - len(kept_classes)]
    slides: list[FeaturedClass | FeaturedEvent] = [
        *(_featured_class(offering) for offering in kept_classes),
        *(_featured_event(event) for event in kept_events),
    ]
    random.shuffle(slides)
    return slides


def _featured_class(offering: ClassOffering) -> FeaturedClass:
    """One class's slide: its next session, instructors, link, and the catalog card's picture."""
    from classes.templatetags.classes_tags import strip_date_suffix

    return FeaturedClass(
        title=strip_date_suffix(offering.title),
        url=reverse("classes:public_class_detail", kwargs={"slug": offering.slug}),
        start=offering.next_session_at,  # type: ignore[attr-defined]  # annotated by featurable()
        instructors=offering.instructor_names,
        photo_url=offering.card_photo_url,
        photo_position=offering.card_object_position if offering.hero_image_url else "50% 50%",
    )


def _featured_event(event: CommunityEvent) -> FeaturedEvent:
    """One event's slide: its next date, links, place, and its photo or else its flyer's QR."""
    start = event.next_occurrence_start()
    photo_url = event.hero_image_url
    place = " · ".join(part for part in (event.area.name if event.area is not None else "", event.location) if part)
    return FeaturedEvent(
        event=event,
        title=event.title,
        url=f"{reverse('hub_event_detail', args=[event.pk])}{event.date_query(start)}",
        flyer_url=reverse("hub_event_flyer", args=[event.pk]),
        start=start,
        place=place,
        photo_url=photo_url,
        photo_position=event.hero_object_position,
        qr_svg="" if photo_url else event.qr_svg(),
    )


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
    """The member's next few horizon items, merged soonest-first and capped.

    Merges the member's own upcoming orientation bookings, the next occurrence of each
    community/guild event visible to them (site-wide + their guilds), and the class
    sessions already materialized onto the calendar (``source="classes"``).
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
        owner_name = booking.orientation_type.owner_name
        items.append(
            UpcomingItem(
                title=f"{owner_name} orientation",
                start=booking.slot.starts_at,
                end=booking.slot.ends_at,
                kind="Orientation",
                url=booking.orientation_type.owner_page_path(),
                location=booking.slot.location,
                guild_name=owner_name,
            )
        )

    # 2. Published community/guild events visible to the member — the next in-window occurrence
    # each. Published only, like the Calendar: a proposal, a parked event or a draft (#831) is
    # not on the Calendar yet, so Home never shows it either.
    for event in CommunityEvent.objects.published().upcoming().for_member(member).select_related("guild"):
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
                url=calendar_url,
                location=event.location,
                guild_name=event.guild.name if event.guild_id else "",
                video_url=event.video_url,
            )
        )

    # 3. Classes at the space — already materialized as source="classes" calendar rows.
    # Only the soonest few can survive the final cap, so bound the fetch instead of pulling
    # every future class session.
    class_events = (
        CalendarEvent.objects.upcoming().filter(source=CalendarEvent.Source.CLASSES).order_by("start_dt")[:UPCOMING_CAP]
    )
    for event in class_events:
        items.append(
            UpcomingItem(
                title=event.title,
                start=event.start_dt,
                end=event.end_dt,
                kind="Class",
                url=event.url or calendar_url,
                location=event.location,
            )
        )

    items.sort(key=lambda item: item.start)
    return items[:UPCOMING_CAP]


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


def _my_guilds(member: Member) -> list[GuildShortcut]:
    """The member's joined guilds, each flagged when they also lead or staff it."""
    staffed_ids = set(member.staffed_guilds.values_list("pk", flat=True))
    return [GuildShortcut(guild=guild, is_staff=guild.pk in staffed_ids) for guild in member.joined_guilds]
