"""Calendar service — fetches iCal feeds, parses events, and upserts CalendarEvent records."""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from datetime import date as date_type
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from functools import partial
from typing import Any

from django.utils import timezone

from core.models import CalendarFeed, SiteConfiguration
from membership.models import CalendarEvent, Guild


def _to_datetime(val: Any) -> datetime:
    """Convert a date or datetime value to a UTC-aware datetime."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=dt_timezone.utc)
        return val.astimezone(dt_timezone.utc)
    return datetime(val.year, val.month, val.day, tzinfo=dt_timezone.utc)


def _parse_ical_events(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Parse raw iCal bytes into a list of event dicts."""
    import icalendar

    cal = icalendar.Calendar.from_ical(raw_bytes)
    events: list[dict[str, Any]] = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", ""))
        if not uid:
            continue

        summary = component.get("SUMMARY", "")
        title = str(summary) if summary else "(No title)"

        dtstart = component.get("DTSTART")
        dtend = component.get("DTEND")
        if dtstart is None:
            continue

        start_val = dtstart.dt
        end_val = dtend.dt if dtend else start_val
        all_day = isinstance(start_val, date_type) and not isinstance(start_val, datetime)

        events.append(
            {
                "uid": uid,
                "title": title,
                "description": str(component.get("DESCRIPTION", "")),
                "location": str(component.get("LOCATION", "")),
                "url": str(component.get("URL", "")),
                "start_dt": _to_datetime(start_val),
                "end_dt": _to_datetime(end_val),
                "all_day": all_day,
            }
        )

    return events


def _fetch_and_parse(url: str) -> list[dict[str, Any]]:
    """Fetch an iCal URL and return parsed event dicts."""
    with urllib.request.urlopen(url, timeout=10) as response:
        raw = response.read()
    return _parse_ical_events(raw)


def _upsert_events(
    events: list[dict[str, Any]],
    guild: Guild | None,
    source: str,
    feed: CalendarFeed | None = None,
) -> int:
    """Insert or update CalendarEvent records for the given source.

    Uniqueness is per-``(guild, feed, uid)`` so two ``CalendarFeed`` rows whose
    upstream calendars happen to share a UID don't clobber each other.
    """
    now = timezone.now()
    for evt in events:
        CalendarEvent.objects.update_or_create(
            guild=guild,
            feed=feed,
            uid=evt["uid"],
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
    events = _fetch_and_parse(guild.calendar_url)
    count = _upsert_events(events, guild=guild, source="guild")
    guild.calendar_last_fetched_at = timezone.now()
    guild.save(update_fields=["calendar_last_fetched_at"])
    return count


def sync_calendar_feed(feed: CalendarFeed) -> int:
    """Fetch and sync one named CalendarFeed. Returns events synced (0 if no URL)."""
    if not feed.ical_url:
        return 0
    events = _fetch_and_parse(feed.ical_url)
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

    qs = ClassSession.objects.filter(
        class_offering__status=ClassOffering.Status.PUBLISHED,
        class_offering__is_private=False,
        starts_at__gte=now,
        starts_at__lte=horizon,
    ).select_related("class_offering", "class_offering__category")

    kept_uids: list[str] = []
    for session in qs:
        offering = session.class_offering
        uid = f"local-class-{session.pk}"
        kept_uids.append(uid)
        CalendarEvent.objects.update_or_create(
            guild=None,
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

    # Purge every "classes" event not backed by a live local session — stale
    # local-class rows and any leftover legacy classes-* rows from the retired
    # Drupal calendar feed.
    CalendarEvent.objects.filter(source="classes").exclude(uid__in=kept_uids).delete()

    config = SiteConfiguration.load()
    config.classes_last_synced_at = now
    config.save(update_fields=["classes_last_synced_at"])
    return len(kept_uids)


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

    config = SiteConfiguration.load()
    if config.legacy_cms_sync_enabled:
        from classes.import_service import sync_legacy_cms

        _run_source("legacy CMS", sync_legacy_cms, errors)

    # Always materialize local plfog classes onto the calendar — independent of the
    # external feed toggles. This is the sole source of class events on the calendar.
    _run_source("local classes", sync_local_class_events, errors)

    return errors
