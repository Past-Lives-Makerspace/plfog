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

if TYPE_CHECKING:
    from membership.models import Member

# How far out "Your upcoming" looks, and how many rows each section shows.
UPCOMING_HORIZON_DAYS = 90
UPCOMING_CAP = 5
ANNOUNCEMENTS_CAP = 4


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
    gate (built ONCE here, not re-derived per property in the template). All business logic
    lives on ``membership`` managers/props; this only fetches, normalizes, sorts, and caps.
    """
    return {
        "upcoming": _upcoming_items(member),
        "announcements": _announcements(member),
        "my_guilds": _my_guilds(member),
        "onboarding": member.onboarding,
        "show_onboarding": member.show_onboarding,
    }


def _upcoming_items(member: Member) -> list[UpcomingItem]:
    """The member's next few horizon items, merged soonest-first and capped.

    Merges the member's own upcoming orientation bookings, the next occurrence of each
    community/guild event visible to them (site-wide + their guilds), and the class
    sessions already materialized onto the calendar (``source="classes"``).
    """
    now = timezone.now()
    today = now.date()
    horizon = today + timedelta(days=UPCOMING_HORIZON_DAYS)
    calendar_url = reverse("hub_community_calendar")

    items: list[UpcomingItem] = []

    # 1. The member's own orientation bookings — clearly personal.
    for booking in member.orientation_bookings.upcoming().select_related("slot", "guild"):
        items.append(
            UpcomingItem(
                title=f"{booking.guild.name} orientation",
                start=booking.slot.starts_at,
                end=booking.slot.ends_at,
                kind="Orientation",
                url=reverse("hub_guild_detail", args=[booking.guild.slug]),
                location=booking.slot.location,
                guild_name=booking.guild.name,
            )
        )

    # 2. Community/guild events visible to the member — the next in-window occurrence each.
    for event in CommunityEvent.objects.upcoming().for_member(member).select_related("guild"):
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
