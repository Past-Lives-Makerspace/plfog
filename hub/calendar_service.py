"""Calendar service — fetches iCal feeds, parses events, and upserts CalendarEvent records."""

from __future__ import annotations

import time
import urllib.request
from collections.abc import Callable
from datetime import date as date_type
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from functools import partial
from typing import TYPE_CHECKING, Any

from django.db.models import Q
from django.utils import timezone

from core.models import CalendarFeed, SiteConfiguration
from membership.models import CalendarEvent, CommunityEvent, Guild

if TYPE_CHECKING:
    from core.integrations.discord_events import DiscordScheduledEventsClient


def _to_datetime(val: Any) -> datetime:
    """Convert a date or datetime value to a UTC-aware datetime."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=dt_timezone.utc)
        return val.astimezone(dt_timezone.utc)
    return datetime(val.year, val.month, val.day, tzinfo=dt_timezone.utc)


# How far back / ahead to expand recurring feeds on each sync. Matches the class calendar's
# 180-day horizon; the one-day look-back keeps an in-progress event visible.
_SYNC_LOOKBACK = timedelta(days=1)
_SYNC_HORIZON = timedelta(days=180)


def _sync_window() -> tuple[datetime, datetime]:
    """The [start, end] datetime window that recurring feeds are expanded over."""
    now = timezone.now()
    return now - _SYNC_LOOKBACK, now + _SYNC_HORIZON


def _event_dict(component: Any, recurrence_id: str) -> dict[str, Any] | None:
    """Build a CalendarEvent dict from an iCal VEVENT (or expanded occurrence).

    Returns ``None`` for a component with no ``UID`` or no ``DTSTART`` (skip it).
    """
    uid = str(component.get("UID", ""))
    dtstart = component.get("DTSTART")
    if not uid or dtstart is None:
        return None
    dtend = component.get("DTEND")
    start_val = dtstart.dt
    end_val = dtend.dt if dtend else start_val
    all_day = isinstance(start_val, date_type) and not isinstance(start_val, datetime)
    summary = component.get("SUMMARY", "")
    return {
        "uid": uid,
        "recurrence_id": recurrence_id,
        "title": str(summary) if summary else "(No title)",
        "description": str(component.get("DESCRIPTION", "")),
        "location": str(component.get("LOCATION", "")),
        "url": str(component.get("URL", "")),
        "start_dt": _to_datetime(start_val),
        "end_dt": _to_datetime(end_val),
        "all_day": all_day,
    }


def _parse_ical_events(raw_bytes: bytes, window_start: datetime, window_end: datetime) -> list[dict[str, Any]]:
    """Parse raw iCal bytes into event dicts, expanding recurring events over the window.

    Two passes, so one-off and repeating events each behave correctly:

    - **One-off events** are stored as-is, any date (unchanged from before) — each keeps
      ``recurrence_id=""`` so an edited time updates the same row.
    - **Recurring events** (a VEVENT carrying an ``RRULE``/``RDATE``) are stored upstream as a
      single master at the series' *original* start — often months or years in the past. Rendered
      as-is, that lone row never lands in the calendar's visible window and every future instance
      silently vanishes (this is why weekly "Open Studio Hours" showed nothing). Instead we let
      ``recurring_ical_events`` materialize each occurrence between ``window_start`` and
      ``window_end`` — honoring ``EXDATE``, single-instance overrides, and timezones — and give
      each occurrence a ``recurrence_id`` so it upserts as its own row rather than collapsing onto
      the series' shared UID.
    """
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(raw_bytes)

    # UIDs whose VEVENT actually repeats — everything else is a plain one-off.
    recurring_uids = {
        str(component.get("UID", ""))
        for component in cal.walk("VEVENT")
        if component.get("RRULE") or component.get("RDATE")
    }
    recurring_uids.discard("")

    events: list[dict[str, Any]] = []

    # Pass 1: one-off events, stored regardless of date.
    for component in cal.walk("VEVENT"):
        if str(component.get("UID", "")) in recurring_uids:
            continue
        evt = _event_dict(component, recurrence_id="")
        if evt is not None:
            events.append(evt)

    # Pass 2: recurring events, one row per occurrence in the window. Expand a sub-calendar of ONLY
    # the recurring components (plus any VTIMEZONE, so TZIDs still resolve). recurring_ical_events
    # raises on a VEVENT with no DTSTART, so isolating the recurring series keeps one malformed
    # one-off elsewhere in the same feed from aborting the whole expansion.
    if recurring_uids:
        recurring_cal = icalendar.Calendar()
        for component in cal.walk():
            name = getattr(component, "name", "")
            if name == "VTIMEZONE" or (name == "VEVENT" and str(component.get("UID", "")) in recurring_uids):
                recurring_cal.add_component(component)
        for occurrence in recurring_ical_events.of(recurring_cal).between(window_start, window_end):
            evt = _event_dict(occurrence, recurrence_id=_to_datetime(occurrence.get("DTSTART").dt).isoformat())
            if evt is not None:
                events.append(evt)

    return events


def _fetch_and_parse(url: str, window_start: datetime, window_end: datetime) -> list[dict[str, Any]]:
    """Fetch an iCal URL and return parsed event dicts expanded over the window."""
    with urllib.request.urlopen(url, timeout=10) as response:
        raw = response.read()
    return _parse_ical_events(raw, window_start, window_end)


def _pushed_event_uids() -> set[str]:
    """iCal UIDs of CommunityEvents we ourselves pushed to Google Calendar.

    The Member/Public calendars we push to are the same calendars the general feeds
    re-import, so our own events come back in the feed under their Google-assigned
    UID. Importing those copies would double-list every pushed event (and the Discord
    mirror would then duplicate its Scheduled Event too) — they must be skipped.
    """
    return set(CommunityEvent.objects.pushed().values_list("google_ical_uid", flat=True))


def _upsert_events(
    events: list[dict[str, Any]],
    guild: Guild | None,
    source: str,
    feed: CalendarFeed | None = None,
) -> int:
    """Insert or update CalendarEvent records for the given source.

    Uniqueness is per-``(guild, feed, uid, recurrence_id)`` so each occurrence of a recurring
    series gets its own row, and two ``CalendarFeed`` rows whose upstream calendars happen to
    share a UID don't clobber each other. Echoes of our own Google-pushed CommunityEvents
    (see :func:`_pushed_event_uids`) are skipped, not imported.
    """
    now = timezone.now()
    echo_uids = _pushed_event_uids()
    events = [evt for evt in events if evt["uid"] not in echo_uids]
    for evt in events:
        CalendarEvent.objects.update_or_create(
            guild=guild,
            feed=feed,
            uid=evt["uid"],
            recurrence_id=evt["recurrence_id"],
            defaults={
                "source": source,
                "title": evt["title"],
                "description": evt["description"],
                "location": evt["location"],
                "url": evt["url"],
                "start_dt": evt["start_dt"],
                "end_dt": evt["end_dt"],
                "all_day": evt["all_day"],
                "fetched_at": now,
            },
        )
    return len(events)


def sync_guild_calendar(guild: Guild) -> int:
    """Fetch and sync a guild's iCal calendar. Returns events synced (0 if no URL)."""
    if not guild.calendar_url:
        return 0
    window_start, window_end = _sync_window()
    events = _fetch_and_parse(guild.calendar_url, window_start, window_end)
    count = _upsert_events(events, guild=guild, source="guild")
    guild.calendar_last_fetched_at = timezone.now()
    guild.save(update_fields=["calendar_last_fetched_at"])
    return count


def sync_calendar_feed(feed: CalendarFeed) -> int:
    """Fetch and sync one named CalendarFeed. Returns events synced (0 if no URL)."""
    if not feed.ical_url:
        return 0
    window_start, window_end = _sync_window()
    events = _fetch_and_parse(feed.ical_url, window_start, window_end)
    count = _upsert_events(events, guild=None, source="general", feed=feed)
    feed.last_fetched_at = timezone.now()
    feed.save(update_fields=["last_fetched_at"])
    return count


def sync_general_calendar() -> int:
    """Fetch and sync every configured CalendarFeed. Returns total events synced."""
    total = 0
    for feed in CalendarFeed.objects.all():
        if not feed.ical_url:
            continue
        total += sync_calendar_feed(feed)
    return total


# Discord Scheduled Events mirror of the general feeds. Each recurring occurrence is its
# own CalendarEvent row, so the full 180-day sync horizon would flood Discord's events
# list — only this near window is pushed, capped well under Discord's 100-per-guild limit.
_DISCORD_PUSH_HORIZON = timedelta(days=30)
_DISCORD_PUSH_CAP = 50
_DISCORD_PUSH_PACE_SECONDS = 0.5  # light pacing between Discord calls so a capped-50
# nightly run doesn't burst the tiny scheduled-events bucket in one breath. Worst case
# (the full cap, no 429s) adds ~25s -- fine for an unattended nightly cron; the
# interactive single-event save (push_community_event) never goes through this loop.


def _discord_event_body(event: CalendarEvent) -> dict[str, Any]:
    """The Discord Scheduled Event payload for a feed CalendarEvent.

    Feed events are physical happenings, so EXTERNAL entity type — which *requires* an
    end time and a non-blank location (a blank location 400s, hence the fallback).
    """
    from core.integrations.discord_events import (
        DEFAULT_LOCATION,
        _DESCRIPTION_MAX,
        _ENTITY_TYPE_EXTERNAL,
        _LOCATION_MAX,
        _NAME_MAX,
        _PRIVACY_GUILD_ONLY,
    )

    description = "\n\n".join(part for part in (event.description, event.url) if part)
    return {
        "name": event.title[:_NAME_MAX],
        "privacy_level": _PRIVACY_GUILD_ONLY,
        "entity_type": _ENTITY_TYPE_EXTERNAL,
        "scheduled_start_time": event.start_dt.isoformat(),
        "scheduled_end_time": event.end_dt.isoformat(),
        "entity_metadata": {"location": (event.location or DEFAULT_LOCATION)[:_LOCATION_MAX]},
        "description": description[:_DESCRIPTION_MAX],
    }


def sync_discord_feed_events() -> int:
    """Mirror upcoming general-feed events into Discord Scheduled Events.

    Pushes every ``source="general"`` event starting within ``_DISCORD_PUSH_HORIZON``
    (capped at ``_DISCORD_PUSH_CAP``, soonest first): creates the Discord event for a row
    without one, updates rows already pushed, and recreates a row whose Discord copy was
    deleted by hand (an update 404). A still-future row that dropped out of the push set
    (date moved, or pushed under an older horizon) has its Discord copy deleted; past
    events are left to Discord's own lifecycle. Echoes of our own Google-pushed
    CommunityEvents are excluded from the push set — the dropped pass deletes any Discord
    copies they gained before echo suppression existed, then the rows themselves are
    purged. Returns the number of events in the push set, or 0 when Discord Events sync
    is disabled.

    Every event is attempted even when one fails; failures are then raised as one
    :class:`DiscordEventsError` so ``sync_all_sources`` records the source as failed.

    Discord calls are lightly paced: the very first call of the run is un-paced, then every
    later call — push OR delete — sleeps ``_DISCORD_PUSH_PACE_SECONDS`` first. Pacing is at
    one-call-per-feed-event granularity; the rare update-then-recreate double-call inside a
    single ``_push_feed_event`` is not internally paced (an isolated one-off, and
    ``_execute``'s own retry covers a transient 429 there).
    """
    from core.integrations.discord_events import DiscordEventsError, DiscordScheduledEventsClient

    client = DiscordScheduledEventsClient.from_settings()
    now = timezone.now()
    echo_uids = _pushed_event_uids()
    if not client.enabled:
        # Echo rows with no live future Discord copy need no API call to clean up —
        # purge them even while Discord sync is off, so they don't linger on the
        # calendar waiting for the toggle.
        _purge_echoed_feed_events(echo_uids, now)
        return 0

    push_set = list(
        CalendarEvent.objects.filter(
            source=CalendarEvent.Source.GENERAL,
            start_dt__gte=now,
            start_dt__lte=now + _DISCORD_PUSH_HORIZON,
        )
        .exclude(uid__in=echo_uids)
        .order_by("start_dt")[:_DISCORD_PUSH_CAP]
    )
    failures: list[str] = []
    made_a_call = False

    def _pace() -> None:
        """Sleep before every Discord call except the first, sharing one run-wide counter."""
        nonlocal made_a_call
        if made_a_call:
            time.sleep(_DISCORD_PUSH_PACE_SECONDS)
        made_a_call = True

    for event in push_set:
        _pace()
        try:
            _push_feed_event(client, event)
        except DiscordEventsError as exc:
            failures.append(f"'{event.title}': {exc}")

    dropped = (
        CalendarEvent.objects.filter(source=CalendarEvent.Source.GENERAL, start_dt__gte=now)
        .exclude(discord_event_id="")
        .exclude(pk__in=[event.pk for event in push_set])
    )
    for event in dropped:
        _pace()
        try:
            _delete_feed_event_copy(client, event)
        except DiscordEventsError as exc:
            failures.append(f"'{event.title}': {exc}")

    _purge_echoed_feed_events(echo_uids, now)

    if failures:
        raise DiscordEventsError(f"{len(failures)} Discord event push(es) failed; first: {failures[0]}")
    return len(push_set)


def _push_feed_event(client: DiscordScheduledEventsClient, event: CalendarEvent) -> None:
    """Create or update the Discord copy of one feed event.

    An update that 404s means the Discord copy was deleted by hand — recreate it
    rather than erroring every morning forever.
    """
    from core.integrations.discord_events import DiscordEventsError

    body = _discord_event_body(event)
    if event.discord_event_id:
        try:
            client.update_event(client.server_id, event.discord_event_id, body, retry_on_rate_limit=True)
        except DiscordEventsError as exc:
            if "404" not in str(exc):
                raise
            event.discord_event_id = ""  # deleted on Discord's side — recreate below
    if not event.discord_event_id:
        created = client.insert_event(client.server_id, body, retry_on_rate_limit=True)
        event.discord_event_id = created["id"]
        event.save(update_fields=["discord_event_id"])


def _delete_feed_event_copy(client: DiscordScheduledEventsClient, event: CalendarEvent) -> None:
    """Delete the Discord copy of a feed event and clear the link (a 404 is already-gone)."""
    from core.integrations.discord_events import DiscordEventsError

    try:
        client.delete_event(client.server_id, event.discord_event_id, retry_on_rate_limit=True)
    except DiscordEventsError as exc:
        if "404" not in str(exc):
            raise
    event.discord_event_id = ""
    event.save(update_fields=["discord_event_id"])


def _purge_echoed_feed_events(echo_uids: set[str], now: datetime) -> None:
    """Delete feed rows that are echoes of our own Google-pushed CommunityEvents.

    Only rows with no live future Discord copy are removed: a future row still holding a
    ``discord_event_id`` means its Discord delete failed this run, so it survives to
    retry next sweep; a past row's Discord copy is already finished and needs no delete.
    """
    CalendarEvent.objects.filter(source=CalendarEvent.Source.GENERAL, uid__in=echo_uids).filter(
        Q(discord_event_id="") | Q(start_dt__lt=now)
    ).delete()


def sync_local_class_events() -> int:
    """Materialize upcoming local plfog class sessions into CalendarEvent rows.

    This is the sole source of ``source="classes"`` events on the Community
    Calendar — each event links to the class on our own site (``/classes/<slug>/``),
    never to the legacy classes.pastlives.space pages. Drupal still feeds the class
    *catalog* via ``classes.import_service.sync_legacy_cms``; those offerings flow
    onto the calendar through here.

    Any ``source="classes"`` event not backed by a live local session is purged at
    the end of each sync — this includes leftover legacy ``classes-*`` events from
    the retired Drupal calendar feed.

    Titles are run through ``strip_date_suffix`` so CMS-imported date suffixes
    (e.g. "Intro to Welding - 6/5/26") don't show on the calendar — matching how
    the public catalog displays them.
    """
    from classes.models import ClassOffering, ClassSession
    from classes.templatetags.classes_tags import strip_date_suffix

    now = timezone.now()
    horizon = now + timedelta(days=180)

    # Gate on the catalog's own bookable() rule so the calendar hides a series the
    # instant its first session begins — exactly as the Class Catalog does. Using a
    # materialized pk list (not class_offering__in=<qs>) keeps bookable()'s internal
    # annotate/order_by/distinct from becoming a correlated subquery. bookable()
    # already enforces published + non-private, so those filters are subsumed.
    bookable_ids = ClassOffering.objects.bookable().values_list("pk", flat=True)
    qs = ClassSession.objects.filter(
        class_offering_id__in=bookable_ids,
        starts_at__gte=now,
        starts_at__lte=horizon,
    ).select_related("class_offering", "class_offering__category")

    kept_pks: list[int] = []
    for session in qs:
        offering = session.class_offering
        uid = f"local-class-{session.pk}"
        event, _created = CalendarEvent.objects.update_or_create(
            guild=offering.category.guild,
            uid=uid,
            defaults={
                "source": "classes",
                "title": strip_date_suffix(offering.title),
                "description": offering.description[:500],
                "location": "Past Lives Makerspace",
                "url": f"/classes/{offering.slug}/",
                "start_dt": session.starts_at,
                "end_dt": session.ends_at,
                "all_day": False,
                "fetched_at": now,
            },
        )
        kept_pks.append(event.pk)

    # Purge every "classes" event row we didn't just touch — stale local-class rows,
    # leftover legacy classes-* rows from the retired Drupal calendar feed, AND the
    # old-guild copy of a class whose category moved guilds (guild is part of the
    # upsert key above, so a re-guilded class lands in a NEW row; matching on uid
    # alone would keep the old copy and double-list the class).
    CalendarEvent.objects.filter(source="classes").exclude(pk__in=kept_pks).delete()

    config = SiteConfiguration.load()
    config.classes_last_synced_at = now
    config.save(update_fields=["classes_last_synced_at"])
    return len(kept_pks)


def _run_source(label: str, sync: Callable[[], object], errors: list[str]) -> None:
    """Run one source sync, capturing any failure into ``errors`` instead of raising.

    Keeps a single unreachable feed from aborting the rest of the daily sweep.
    """
    try:
        sync()
    except Exception as exc:  # noqa: BLE001 — isolate per-source so one failure can't stop the others
        errors.append(f"{label}: {exc}")


def sync_all_sources() -> list[str]:
    """Refresh every calendar and class source into the database.

    Run once each morning by the ``sync_all_sources`` management command (a Render
    cron). The Community Calendar and the book/CMS pages read the resulting
    ``CalendarEvent`` and ``ClassOffering`` rows straight from the database — they
    never fetch upstream on page load.

    Each source syncs in isolation. Returns a list of human-readable error messages
    (empty when every source succeeded) so the caller can fail loudly.
    """
    errors: list[str] = []

    for guild in Guild.objects.filter(is_active=True, calendar_url__gt=""):
        _run_source(f"guild '{guild}'", partial(sync_guild_calendar, guild), errors)

    for feed in CalendarFeed.objects.filter(ical_url__gt=""):
        _run_source(f"feed '{feed}'", partial(sync_calendar_feed, feed), errors)

    # Mirror the freshly-synced general feeds into Discord Scheduled Events (no-op when
    # Discord Events sync is off).
    _run_source("discord events", sync_discord_feed_events, errors)

    config = SiteConfiguration.load()
    if config.legacy_cms_sync_enabled:
        from classes.import_service import sync_legacy_cms

        _run_source("legacy CMS", sync_legacy_cms, errors)

    # Always materialize local plfog classes onto the calendar — independent of the
    # external feed toggles. This is the sole source of class events on the calendar.
    _run_source("local classes", sync_local_class_events, errors)

    return errors
