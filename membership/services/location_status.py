"""Whether each of a guild's locations is free, about to be used, or in use now (#616).

One function, :func:`guild_location_statuses`, feeds the light on the guild page. It reads
four sources, each in one query whatever the number of locations:

- sessions of published classes (``ClassOffering.area``); a private class counts but is named
  only "Private class",
- orientation slots that are not cancelled and hold at least one active booking
  (``OrientationType.area``; a slot takes its type's location),
- published events, a repeating one through :meth:`CommunityEvent.occurrences_in`
  (``CommunityEvent.area``); studio hours are left out, because they are the guild's own
  open hours rather than something that takes the area away from members,
- confirmed equipment reservations (``Equipment.area``; a reservation takes its equipment's).

A location also counts what happens in the locations it shares space with. The title shown is
the class title, the orientation type's name, the event title, or "Reserved: <equipment>";
never a member's name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.db.models import Exists, OuterRef, Prefetch, Q
from django.db.models import TextChoices
from django.utils import timezone

if TYPE_CHECKING:
    from membership.models import Guild, Location

#: What a private class is called on the light. It still takes the area, but its title stays off the
#: guild page, which also renders on the public guilds site, as it stays off every other public surface.
PRIVATE_CLASS_TITLE = "Private class"

#: How far ahead something starting counts as "starting soon".
SOON_WINDOW = timedelta(minutes=60)


class Light(TextChoices):
    """The three states of a location's light."""

    FREE = "free", "Free now"
    SOON = "soon", "Starting soon"
    IN_USE = "in_use", "In use"


@dataclass(frozen=True)
class AreaActivity:
    """One thing using a location for a stretch of time."""

    location_id: int
    title: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class LocationStatus:
    """A location's light and the activity that set it (``None`` when free)."""

    location: Location
    light: str
    activity: AreaActivity | None
    now: datetime

    @property
    def message(self) -> str:
        """The line shown beside the light."""
        if self.activity is None:
            return "Free now"
        if self.light == Light.IN_USE:
            until = _time_label(self.activity.ends_at, self.now)
            return f"In use: {self.activity.title} until {until}. This area might not be available."
        return f"Starting soon: {self.activity.title} at {_time_label(self.activity.starts_at, self.now)}"


def _time_label(when: datetime, now: datetime) -> str:
    """Portland clock time, with the date when it is not today ("3:30 PM", "Oct 6, 9:00 AM")."""
    local = timezone.localtime(when)
    clock = local.strftime("%-I:%M %p")
    if local.date() == timezone.localtime(now).date():
        return clock
    return f"{local.strftime('%b %-d')}, {clock}"


def _class_activities(area_ids: set[int], now: datetime, horizon: datetime) -> list[AreaActivity]:
    """Sessions of published classes overlapping ``[now, horizon]``; demo classes follow the catalog gate."""
    from classes.models import DEMO_SLUG_PREFIX, ClassOffering, ClassSession
    from core.models import SiteConfiguration

    sessions = ClassSession.objects.filter(
        class_offering__area_id__in=area_ids,
        class_offering__status=ClassOffering.Status.PUBLISHED,
        starts_at__lte=horizon,
        ends_at__gt=now,
    ).select_related("class_offering")
    if not SiteConfiguration.load().display_demo_classes:
        sessions = sessions.exclude(class_offering__slug__startswith=DEMO_SLUG_PREFIX)
    return [
        AreaActivity(
            location_id=session.class_offering.area_id,  # type: ignore[arg-type]  # filtered non null above
            title=PRIVATE_CLASS_TITLE if session.class_offering.is_private else session.class_offering.title,
            starts_at=session.starts_at,
            ends_at=session.ends_at,
        )
        for session in sessions
    ]


def _orientation_activities(area_ids: set[int], now: datetime, horizon: datetime) -> list[AreaActivity]:
    """Uncancelled orientation slots with at least one active booking, overlapping the window."""
    from membership.models import OrientationBooking, OrientationSlot

    booked = OrientationBooking.objects.active().filter(slot=OuterRef("pk"))
    slots = (
        OrientationSlot.objects.filter(
            orientation_type__area_id__in=area_ids,
            is_cancelled=False,
            starts_at__lte=horizon,
            ends_at__gt=now,
        )
        .filter(Exists(booked))
        .select_related("orientation_type")
    )
    return [
        AreaActivity(
            location_id=slot.orientation_type.area_id,  # type: ignore[arg-type]  # filtered non null above
            title=slot.orientation_type.name,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
        )
        for slot in slots
    ]


def _event_activities(area_ids: set[int], now: datetime, horizon: datetime) -> list[AreaActivity]:
    """Published events overlapping the window; a repeating one is expanded by its own occurrences_in."""
    from membership.models import CommunityEvent

    none = CommunityEvent.Recurrence.NONE
    events = (
        CommunityEvent.objects.published()
        .exclude(event_type=CommunityEvent.EventType.STUDIO_HOURS)
        .filter(area_id__in=area_ids)
        .filter(
            Q(recurrence=none, starts_at__lte=horizon, ends_at__gt=now)
            | (~Q(recurrence=none) & Q(starts_at__lte=horizon))
        )
    )
    # An occurrence that began yesterday evening can still be running now, so the expansion
    # starts a day back; overlap with the window is what decides.
    first_day = timezone.localtime(now).date() - timedelta(days=1)
    last_day = timezone.localtime(horizon).date()
    activities: list[AreaActivity] = []
    for event in events:
        duration = event.ends_at - event.starts_at
        starts = [event.starts_at] if event.recurrence == none else event.occurrences_in(first_day, last_day)
        for start in starts:
            end = start + duration
            if start <= horizon and end > now:
                activities.append(
                    AreaActivity(
                        location_id=event.area_id,  # type: ignore[arg-type]  # filtered non null above
                        title=event.title,
                        starts_at=start,
                        ends_at=end,
                    )
                )
    return activities


def _reservation_activities(area_ids: set[int], now: datetime, horizon: datetime) -> list[AreaActivity]:
    """Confirmed equipment reservations overlapping the window. The member who reserved is never read."""
    from membership.models import EquipmentReservation

    reservations = (
        EquipmentReservation.objects.confirmed()
        .filter(equipment__area_id__in=area_ids, starts_at__lte=horizon, ends_at__gt=now)
        .select_related("equipment")
    )
    return [
        AreaActivity(
            location_id=reservation.equipment.area_id,  # type: ignore[arg-type]  # filtered non null above
            title=f"Reserved: {reservation.equipment.name}",
            starts_at=reservation.starts_at,
            ends_at=reservation.ends_at,
        )
        for reservation in reservations
    ]


def _status_for(location: Location, activities: list[AreaActivity], now: datetime) -> LocationStatus:
    """Pick the light from the activities in this location and the ones it shares space with.

    In use beats starting soon. Of several in use, the one ending last names how long the area
    stays busy; of several starting soon, the first to start is named.
    """
    in_use = [activity for activity in activities if activity.starts_at <= now]
    if in_use:
        latest = max(in_use, key=lambda activity: activity.ends_at)
        return LocationStatus(location=location, light=Light.IN_USE, activity=latest, now=now)
    if activities:
        first = min(activities, key=lambda activity: activity.starts_at)
        return LocationStatus(location=location, light=Light.SOON, activity=first, now=now)
    return LocationStatus(location=location, light=Light.FREE, activity=None, now=now)


def guild_location_statuses(guild: Guild, now: datetime | None = None) -> list[LocationStatus]:
    """The light for each of the guild's active locations, in name order.

    Empty (after one query) when the guild has no active location. Otherwise a fixed number of
    queries: the locations with their linked ones, then one per source.

    Args:
        guild: The guild whose page is rendering.
        now: The moment to judge against; the current time when omitted (specs pin it).

    Returns:
        One :class:`LocationStatus` per active location of the guild.
    """
    from membership.models import Location

    now = now or timezone.now()
    horizon = now + SOON_WINDOW
    locations = list(
        Location.objects.active()
        .filter(guild=guild)
        .prefetch_related(Prefetch("shares_space_with", queryset=Location.objects.order_by()))
    )
    if not locations:
        return []
    covered: dict[int, set[int]] = {
        location.pk: {location.pk, *(linked.pk for linked in location.shares_space_with.all())}
        for location in locations
    }
    area_ids = set().union(*covered.values())
    activities = [
        *_class_activities(area_ids, now, horizon),
        *_orientation_activities(area_ids, now, horizon),
        *_event_activities(area_ids, now, horizon),
        *_reservation_activities(area_ids, now, horizon),
    ]
    return [
        _status_for(
            location,
            [activity for activity in activities if activity.location_id in covered[location.pk]],
            now,
        )
        for location in locations
    ]
