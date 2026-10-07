"""Lightweight calendar-entry wrappers.

A guild's CMS classes and orientation slots don't live in ``CalendarEvent`` (the
iCal read-through cache) — class events there have ``guild=None`` and orientation
slots aren't calendar events at all. To show them on the shared calendar grid we
wrap them in objects that duck-type the attributes the calendar templates read
(``templates/hub/partials/calendar_content.html`` and ``calendar_event_item.html``):
``pk, title, start_dt, end_dt, all_day, source, source_key, is_in_progress, url,
location, description, guild, feed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, quote_plus, urlencode

from django.utils import timezone

if TYPE_CHECKING:
    from collections.abc import Iterable

    from core.models import SiteConfiguration
    from membership.models import CommunityEvent, Equipment, Guild, OrientationType

# Offsets keep synthetic pks clear of real CalendarEvent pks so the shared
# focusEvent() JS and the month_event_pages map keep working untouched.
CLASS_PK_OFFSET = 1_000_000_000
ORIENTATION_PK_OFFSET = 2_000_000_000
EVENT_PK_OFFSET = 3_000_000_000
RESERVATION_PK_OFFSET = 4_000_000_000
_OCC_STRIDE = 100  # max occurrences per event per window (a few months of monthly « 100)

# How far ahead the Events tab looks for a recurring series' next occurrence. A
# year comfortably contains the next hit for any monthly/weekly cadence, so each
# recurring event resolves to one upcoming row instead of its stale anchor date.
_EVENTS_TAB_HORIZON_DAYS = 365


def calendar_day(event: Any) -> date:
    """The local (Portland) date a calendar grid and list draw ``event`` on.

    ``start_dt`` is stored in UTC, so its own ``.date()`` put every event from 5 PM Pacific
    (4 PM in winter) on the next day's cell. A synced all-day event is anchored to local
    midnight (``hub.calendar_service._to_datetime``), which local time keeps on its day; one
    stored at UTC midnight (a bare date read without that anchor) keeps the date it names
    rather than sliding back to the evening before.
    """
    start = event.start_dt
    if event.all_day:
        utc_start = start.astimezone(UTC)
        if utc_start.time() == time.min:
            return utc_start.date()
    return timezone.localtime(start).date()


@dataclass
class CalendarEntry:
    """Duck-types the CalendarEvent attributes the calendar templates read."""

    pk: int
    title: str
    start_dt: datetime
    end_dt: datetime
    source: str
    url: str = ""
    location: str = ""
    description: str = ""
    video_url: str = ""
    all_day: bool = False
    guild: Guild | None = None
    feed: None = None
    # The backing FOG-native event for a "community" entry, so the templates can draw
    # its Google-sync flag and (on the Events tab) its admin Edit/Delete controls.
    # Always None for feed / class / orientation entries.
    community_event: CommunityEvent | None = None
    # Legend key of the CalendarFeed mirroring this event's Google calendar target
    # (e.g. "feed-2"), stamped only on site-wide community entries so a FOG event
    # groups under the matching feed chip instead of a separate "Events" chip.
    # Empty = no mapping → source_key falls back to the raw source ("community").
    feed_key: str = ""
    # The legend chip this entry toggles and colors with on a calendar that passes its own
    # legend (the Orientations and Reservations calendars, #502): an owning guild's pk,
    # "makerspace", or an item's pk. Wins over feed_key; empty everywhere else.
    legend_key: str = ""
    # Shorter text for the grid chip; the list below and the chip's title= keep ``title``.
    chip_title: str = ""
    # The owner line under the title in the list ("Lathe · Woodworking Guild"), for entries
    # whose owner is not simply ``guild``. Empty = the list's usual guild / feed line.
    owner_label: str = ""

    @property
    def source_key(self) -> str:
        # A page legend key wins first, then a stamped feed key: the entry toggles and
        # colors with that chip.
        if self.legend_key:
            return self.legend_key
        if self.feed_key:
            return self.feed_key
        # Only classes route by their guild's color. Orientation stays "orientation"
        # and community stays "community" even though those entries also carry a guild
        # object — don't recolor them by guild.
        if self.source == "classes" and self.guild is not None:
            return str(self.guild.pk)
        return self.source

    @property
    def is_in_progress(self) -> bool:
        if self.all_day:
            return False
        return self.start_dt <= timezone.now() < self.end_dt


def guild_calendar_entries(guild: Guild, fetch_from: date, fetch_to: date) -> list[CalendarEntry]:
    """Build synthetic calendar entries for a guild's published class sessions and
    upcoming orientation slots whose start date falls within ``[fetch_from, fetch_to]``."""
    from django.urls import reverse

    from classes.models import ClassOffering, ClassSession

    entries: list[CalendarEntry] = []

    # Gate on the catalog's bookable() rule (published + non-private + not-yet-started)
    # so per-guild calendars drop a started series exactly as the catalog and the
    # Community Calendar do. Materialized pk list avoids a correlated subquery.
    bookable_ids = ClassOffering.objects.bookable().values_list("pk", flat=True)
    sessions = ClassSession.objects.filter(
        class_offering__category__guild=guild,
        class_offering_id__in=bookable_ids,
        starts_at__date__gte=fetch_from,
        starts_at__date__lte=fetch_to,
    ).select_related("class_offering")
    for session in sessions:
        offering = session.class_offering
        entries.append(
            CalendarEntry(
                pk=CLASS_PK_OFFSET + session.pk,
                title=offering.title,
                start_dt=session.starts_at,
                end_dt=session.ends_at,
                source="classes",
                url=reverse("classes:register", args=[offering.slug]),
                guild=guild,
            )
        )

    slots = guild.orientation_slots.upcoming().filter(starts_at__date__gte=fetch_from, starts_at__date__lte=fetch_to)
    for slot in slots:
        entries.append(
            CalendarEntry(
                pk=ORIENTATION_PK_OFFSET + slot.pk,
                title="Orientation",
                start_dt=slot.starts_at,
                end_dt=slot.ends_at,
                source="orientation",
                location=slot.location,
                guild=guild,
            )
        )

    return entries


#: The Orientations calendar's legend key for types of equipment no guild owns.
MAKERSPACE_LEGEND_KEY = "makerspace"


def _equipment_owner_label(equipment: Equipment) -> str:
    """ "Lathe · Woodworking Guild", or "Loading dock · Makerspace" for a standalone item."""
    return f"{equipment.name} · {equipment.guild.name if equipment.guild else 'Makerspace'}"


def orientation_legend_key(orientation_type: OrientationType) -> str:
    """The Orientations calendar chip a type files under: its guild, its equipment's guild, or Makerspace."""
    if orientation_type.equipment is not None:
        guild_id = orientation_type.equipment.guild_id
        return str(guild_id) if guild_id is not None else MAKERSPACE_LEGEND_KEY
    return str(orientation_type.guild_id)


def orientation_page_entries(types: Iterable[OrientationType], fetch_from: date, fetch_to: date) -> list[CalendarEntry]:
    """Every bookable slot with a seat left, of ``types``, starting in ``[fetch_from, fetch_to]``.

    The Orientations calendar (#502): the chip says the type, the list says the type, its
    owner and the seats left, and each entry links the type's card on the List view. A
    full slot and a cancelled one are absent: ``bookable()`` drops the cancelled, closed
    and departed, and the seat annotation drops the full, all in one query.
    """
    from membership.models import OrientationSlot

    slots = (
        OrientationSlot.objects.bookable()
        .filter(orientation_type__in=list(types), starts_at__date__gte=fetch_from, starts_at__date__lte=fetch_to)
        .with_seat_holding_count()
        .select_related(
            "orientation_type",
            "orientation_type__guild",
            "orientation_type__equipment",
            "orientation_type__equipment__guild",
        )
        .order_by("starts_at")
    )
    entries: list[CalendarEntry] = []
    for slot in slots:
        if slot.is_full:
            continue
        orientation_type = slot.orientation_type
        seats = slot.seats_remaining
        owner_label = (
            _equipment_owner_label(orientation_type.equipment)
            if orientation_type.equipment is not None
            else orientation_type.owner_name
        )
        entries.append(
            CalendarEntry(
                pk=ORIENTATION_PK_OFFSET + slot.pk,
                title=f"{orientation_type.name} · {orientation_type.owner_name} · {seats} seat{'' if seats == 1 else 's'} left",
                chip_title=orientation_type.name,
                start_dt=slot.starts_at,
                end_dt=slot.ends_at,
                source="orientation",
                url=orientation_type.orientations_page_path(),
                location=slot.location,
                legend_key=orientation_legend_key(orientation_type),
                owner_label=owner_label,
            )
        )
    return entries


def _item_day_url(equipment: Equipment, moment: datetime) -> str:
    """The item's page, opened on the local day of ``moment`` when its schedule can show that day.

    The schedule shows today through the booking horizon (:meth:`Equipment.is_within_horizon`);
    a past booking, or one beyond the horizon, links the bare page rather than a ``?day=``
    the page would ignore.
    """
    from django.urls import reverse

    page = reverse("hub_equipment_detail", args=[equipment.slug])
    day = timezone.localdate(moment)
    return f"{page}?day={day.isoformat()}" if equipment.is_within_horizon(day) else page


def reservation_entries(items: Iterable[Equipment], fetch_from: date, fetch_to: date) -> list[CalendarEntry]:
    """What is taken on ``items`` in ``[fetch_from, fetch_to]``: confirmed reservations and booked orientations.

    The Reservations calendar (#502). A reservation reads "Laser cutter · Sam R."; an
    orientation slot holding a seat on an item's own type reads "Lathe · Orientation",
    because a booked orientation occupies the machine. Every entry files under its item's
    legend chip and links the item's page on that day. A cancelled reservation and an
    open, unbooked slot are absent. Two queries however many items.
    """
    from membership.models import EquipmentReservation, OrientationSlot

    item_list = list(items)
    entries: list[CalendarEntry] = []
    reservations = (
        EquipmentReservation.objects.confirmed()
        .reservations()  # a manager's block stays off the calendar feed (#657)
        .filter(equipment__in=item_list, starts_at__date__gte=fetch_from, starts_at__date__lte=fetch_to)
        .select_related("equipment", "equipment__guild", "member")
        .order_by("starts_at")
    )
    for reservation in reservations:
        item = reservation.equipment
        entries.append(
            CalendarEntry(
                pk=RESERVATION_PK_OFFSET + reservation.pk,
                title=f"{item.name} · {reservation.member.short_name}",
                start_dt=reservation.starts_at,
                end_dt=reservation.ends_at,
                source="reservation",
                url=_item_day_url(item, reservation.starts_at),
                legend_key=str(item.pk),
                owner_label=_equipment_owner_label(item),
            )
        )
    holds = (
        OrientationSlot.objects.holding_seats()
        .filter(
            orientation_type__equipment__in=item_list,
            starts_at__date__gte=fetch_from,
            starts_at__date__lte=fetch_to,
        )
        .select_related("orientation_type__equipment", "orientation_type__equipment__guild")
        .order_by("starts_at")
    )
    for slot in holds:
        # The query keeps equipment owned types only, so the type always has its item.
        item = cast("Equipment", slot.orientation_type.equipment)
        entries.append(
            CalendarEntry(
                pk=ORIENTATION_PK_OFFSET + slot.pk,
                title=f"{item.name} · Orientation",
                start_dt=slot.starts_at,
                end_dt=slot.ends_at,
                source="orientation",
                url=_item_day_url(item, slot.starts_at),
                location=slot.location,
                legend_key=str(item.pk),
                owner_label=_equipment_owner_label(item),
            )
        )
    return entries


def google_target_feed_keys() -> dict[str, str]:
    """Map each Google calendar target (``"public"``/``"member"``) to the legend key
    of the ``CalendarFeed`` that mirrors that Google calendar.

    A feed "mirrors" a target when its iCal URL embeds the target's configured
    calendar id (raw or percent-encoded — Google iCal URLs encode the ``@``). A
    target whose calendar id is unset, or matches no feed, is simply absent from
    the result, so callers fall back to the generic "community" key for it.
    """
    from core.models import CalendarFeed, SiteConfiguration

    config = SiteConfiguration.load()
    target_ids = {
        "member": config.member_google_calendar_id,
        "public": config.public_google_calendar_id,
    }
    feeds = list(CalendarFeed.objects.filter(ical_url__gt=""))
    keys: dict[str, str] = {}
    for target, calendar_id in target_ids.items():
        if not calendar_id:
            continue
        for feed in feeds:
            if calendar_id in feed.ical_url or quote(calendar_id, safe="") in feed.ical_url:
                keys[target] = f"feed-{feed.pk}"
                break
    return keys


def community_event_entries(fetch_from: date, fetch_to: date, guild: Guild | None = None) -> list[CalendarEntry]:
    """Build synthetic calendar entries for FOG-native ``CommunityEvent`` rows that
    contribute an occurrence to ``[fetch_from, fetch_to]``.

    A monthly series expands to one entry per in-window occurrence; a non-recurring
    event yields a single entry. ``guild=None`` returns site-wide + every guild's
    events (the Community Calendar); a guild returns just that guild's events.
    """
    from membership.models import CommunityEvent

    # Only PUBLISHED events surface on the calendar — pending/changes-requested/declined
    # member proposals must never leak onto the public grid.
    qs = CommunityEvent.objects.published().candidates_for_window(fetch_from, fetch_to)
    if guild is not None:
        qs = qs.for_guild(guild)

    # Site-wide entries adopt the feed chip matching their Google target, so the
    # main Calendar legend needs no separate chip of its own. Guild-scoped
    # entries stay unmapped — the guild calendar keeps its own generic chip.
    feed_keys = google_target_feed_keys() if guild is None else {}

    entries: list[CalendarEntry] = []
    for ev in qs.select_related("guild"):
        duration = ev.ends_at - ev.starts_at
        for i, occ_start in enumerate(ev.occurrences_in(fetch_from, fetch_to)):
            entries.append(
                CalendarEntry(
                    # Unique synthetic pk per occurrence: base offset + ev.pk*stride + index.
                    pk=EVENT_PK_OFFSET + ev.pk * _OCC_STRIDE + i,
                    title=ev.title,
                    start_dt=occ_start,
                    end_dt=occ_start + duration,
                    source="community",
                    url=ev.public_url_on(occ_start),
                    location=ev.location,
                    description=ev.description,
                    video_url=ev.video_url,
                    all_day=False,
                    guild=ev.guild,
                    community_event=ev,
                    feed_key=feed_keys.get(ev.google_calendar_target, ""),
                )
            )
    return entries


def upcoming_calendar_events() -> list[Any]:
    """Every upcoming event on the shared Community Calendar as one sorted list.

    This is the source for the Community Calendar's **Events tab**, built so the tab
    lists nothing narrower than the grid: the read-through feed / general / class
    ``CalendarEvent`` rows (echo-deduped exactly like the grid) *plus* one entry per
    published ``CommunityEvent`` at its next occurrence. A recurring series collapses
    to a single upcoming row (its next hit), so admins get one Edit/Delete affordance
    per event rather than one per occurrence.
    """
    from membership.models import CalendarEvent, CommunityEvent

    now = timezone.now()
    # The local date: from 5 PM in Portland the UTC date is tomorrow, which skipped tonight's date.
    today = timezone.localdate(now)
    horizon = today + timedelta(days=_EVENTS_TAB_HORIZON_DAYS)

    # Feed / general / class iCal rows still upcoming, minus the iCal echo of any event
    # FOG itself pushed to Google (matched by the stored google_ical_uid) — the same
    # de-dup the grid applies, so a FOG event is never listed twice.
    pushed_uids = CommunityEvent.objects.pushed().values_list("google_ical_uid", flat=True)
    entries: list[Any] = list(
        CalendarEvent.objects.upcoming().exclude(uid__in=pushed_uids).select_related("guild", "feed")
    )

    # One entry per published CommunityEvent, anchored on its next (or in-progress) occurrence.
    # Same feed-key stamping as the site-wide grid, so the Events tab lists each FOG
    # event under the same chip/color as its grid rendering.
    feed_keys = google_target_feed_keys()
    for ev in CommunityEvent.objects.published().upcoming().select_related("guild"):
        duration = ev.ends_at - ev.starts_at
        upcoming_occ = [occ for occ in ev.occurrences_in(today, horizon) if occ + duration >= now]
        # No in-window occurrence → a still-running non-recurring event (started before
        # today) or one starting past the horizon: fall back to its own start.
        start = upcoming_occ[0] if upcoming_occ else ev.starts_at
        entries.append(
            CalendarEntry(
                pk=EVENT_PK_OFFSET + ev.pk * _OCC_STRIDE,
                title=ev.title,
                start_dt=start,
                end_dt=start + duration,
                source="community",
                url=ev.public_url_on(start),
                location=ev.location,
                description=ev.description,
                video_url=ev.video_url,
                guild=ev.guild,
                community_event=ev,
                feed_key=feed_keys.get(ev.google_calendar_target, ""),
            )
        )
    return sorted(entries, key=lambda e: e.start_dt)


def google_calendar_subscribe_url(calendar_id: str) -> str:
    """A ``webcal://`` subscribe URL for a Google Calendar's public iCal feed.

    Members' calendar apps open a ``webcal://`` link as a live subscription (Apple
    Calendar, Google Calendar, Outlook), so the feed stays up to date. Returns an
    empty string when no calendar id is configured, so the template can hide the link.

    Args:
        calendar_id: The Google Calendar ID (e.g. ``"...@group.calendar.google.com"``).
    """
    if not calendar_id:
        return ""
    return f"webcal://calendar.google.com/calendar/ical/{quote(calendar_id, safe='')}/public/basic.ics"


def google_calendar_add_url(calendar_id: str) -> str:
    """Google Calendar's own "add this calendar" link for a public calendar.

    Android has no handler for ``webcal://``, in Chrome or in the app, and Google Calendar
    on the desktop does not open one either. This link opens the Google Calendar app or
    site with the calendar ready to add, and being a plain ``https`` URL on another host,
    the native shells hand it to the system. Returns an empty string when no calendar id
    is configured, so the template can hide the link.
    """
    if not calendar_id:
        return ""
    return f"https://calendar.google.com/calendar/r?cid={quote(calendar_id, safe='')}"


def calendar_subscribe_links(config: SiteConfiguration) -> list[dict[str, str]]:
    """The Subscribe menu's rows: each configured Google calendar with both link forms.

    An event is pushed to exactly one of the two calendars (``CommunityEvent.google_calendar_target``),
    so a member who wants everything subscribes to both. The menu groups the rows by calendar
    app (Apple Calendar via ``webcal``, Google Calendar via its add link), because that is the
    choice a member actually makes. A calendar with no id configured has no row.
    """
    rows = []
    for key, label, calendar_id in (
        ("member", "Member calendar", config.member_google_calendar_id),
        ("public", "Public calendar", config.public_google_calendar_id),
    ):
        if calendar_id:
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "webcal_url": google_calendar_subscribe_url(calendar_id),
                    "google_url": google_calendar_add_url(calendar_id),
                }
            )
    return rows


# Google rejects a link past roughly 8 KB. The limit is on the encoded description, because
# accented text and emoji encode to many bytes per character.
_GOOGLE_EVENT_DETAILS_LIMIT = 3000


def _trim_to_encoded_length(text: str, limit: int) -> str:
    """The longest prefix of ``text`` whose URL encoding fits in ``limit`` bytes."""
    used = 0
    for index, char in enumerate(text):
        used += len(quote_plus(char))
        if used > limit:
            return text[:index]
    return text


def google_calendar_event_url(event: CommunityEvent, start: datetime, page_url: str = "") -> str:
    """Google Calendar's "create this event" link for one event, for the Add to calendar menu.

    Opens Google Calendar (the app on a phone, the site on a desktop) with the title, times,
    place and description filled in, so a member adds the one event they care about rather
    than subscribing to the whole makerspace calendar. A plain ``https`` URL on another host,
    so it works in every browser and the native shells hand it to the system, where the
    ``.ics`` download does nothing. The times are the date that begins at ``start``, the one
    the event page shows (``CommunityEvent.occurrence_start``), and a recurring series carries
    its ``RRULE`` from there, as the ``.ics`` does. The details end with the video link, when
    there is one, and the event page's link, so the entry leads back here.

    Times go in the makerspace's local time with its zone named (``ctz``), because the
    ``RRULE``'s weekday is the local one: in UTC an evening series would land a day late for
    anyone whose own calendar is not on Pacific time.
    """
    details = _trim_to_encoded_length(event.description, _GOOGLE_EVENT_DETAILS_LIMIT)
    for link in (event.video_url, page_url):
        if link:
            details = f"{details}\n\n{link}" if details else link
    params = {
        "action": "TEMPLATE",
        "text": event.title,
        "dates": "/".join(
            timezone.localtime(moment).strftime("%Y%m%dT%H%M%S")
            for moment in (start, start + (event.ends_at - event.starts_at))
        ),
        "ctz": timezone.get_default_timezone_name(),
    }
    if details:
        params["details"] = details
    if event.location:
        params["location"] = event.location
    rrule = event.ical_rrule()
    if rrule:
        params["recur"] = f"RRULE:{rrule}"
    return f"https://calendar.google.com/calendar/render?{urlencode(params)}"
