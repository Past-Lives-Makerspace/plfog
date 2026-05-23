"""Calendar service — fetches iCal feeds, parses events, and upserts CalendarEvent records."""

from __future__ import annotations

import json
import urllib.request
from datetime import date as date_type
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

from django.utils import timezone
from django.utils.html import strip_tags

from core.models import CalendarFeed, SiteConfiguration
from membership.models import CalendarEvent, Guild

CLASSES_API_BASE = "https://classes.pastlives.space"
CLASSES_API_URL = f"{CLASSES_API_BASE}/jsonapi/node/class"

DEFAULT_MAX_AGE_SECONDS = 900  # 15 minutes


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


def _fetch_json(url: str) -> dict[str, Any]:
    """Fetch a JSON:API URL and return the parsed response."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.api+json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read())


def sync_classes_calendar() -> int:
    """Sync upcoming classes from classes.pastlives.space. Returns number of events upserted.

    Each class session becomes a CalendarEvent with guild=None and source="classes".
    The event URL points to the registration page on classes.pastlives.space.
    """
    config = SiteConfiguration.load()
    if not config.sync_classes_enabled:
        return 0

    now = timezone.now()
    total = 0

    next_url: str | None = CLASSES_API_URL
    while next_url:
        data = _fetch_json(next_url)

        for item in data.get("data", []):
            attrs = item.get("attributes") or {}

            dates = attrs.get("field_dates") or []
            if not dates:
                continue

            # Registration URL
            path_alias = (attrs.get("path") or {}).get("alias", "")
            event_url = f"{CLASSES_API_BASE}{path_alias}" if path_alias else ""

            # Description: strip HTML from body
            body = attrs.get("body") or {}
            description = strip_tags(body.get("value") or body.get("summary") or "")[:500]

            title = attrs.get("title") or "(No title)"

            # One CalendarEvent per session date
            for i, session in enumerate(dates):
                start_str = session.get("value")
                end_str = session.get("end_value") or start_str
                if not start_str:
                    continue

                start_dt = datetime.fromisoformat(start_str)
                end_dt = datetime.fromisoformat(end_str)

                uid = f"classes-{item['id']}-{i}"

                # Remove any old record that was stored with a guild (pre-migration data)
                CalendarEvent.objects.filter(uid=uid).exclude(guild=None).delete()

                CalendarEvent.objects.update_or_create(
                    guild=None,
                    uid=uid,
                    defaults={
                        "source": "classes",
                        "title": title,
                        "description": description,
                        "location": "",
                        "url": event_url,
                        "start_dt": start_dt,
                        "end_dt": end_dt,
                        "all_day": False,
                        "fetched_at": now,
                    },
                )
                total += 1

        next_url = ((data.get("links") or {}).get("next") or {}).get("href")

    config.classes_last_synced_at = now
    config.save(update_fields=["classes_last_synced_at"])
    return total


def sync_local_class_events() -> int:
    """Materialize upcoming local plfog class sessions into CalendarEvent rows.

    Complements the Drupal-fed ``sync_classes_calendar`` during the transition
    period (when ``SiteConfiguration.sync_classes_enabled`` is still on). Both
    sources live under ``source="classes"`` but use distinct UID prefixes so
    they don't collide.

    Stale local events (class archived/unpublished, or session deleted) are
    cleaned up at the end of each sync.
    """
    from classes.models import ClassOffering, ClassSession

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
                "title": offering.title,
                "description": offering.description[:500],
                "location": "Past Lives Makerspace",
                "url": f"/classes/{offering.slug}/",
                "start_dt": session.starts_at,
                "end_dt": session.ends_at,
                "all_day": False,
                "fetched_at": now,
            },
        )

    # Purge stale local-class events that no longer map to a live session.
    CalendarEvent.objects.filter(source="classes", uid__startswith="local-class-").exclude(uid__in=kept_uids).delete()
    return len(kept_uids)


def refresh_stale_sources(max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> None:
    """Refresh any calendar sources not synced within max_age_seconds.

    Called by the calendar_events_partial view on each HTMX poll.
    Exceptions from individual sources are caught so one bad calendar
    cannot crash the page for all users.
    """
    cutoff = timezone.now() - timedelta(seconds=max_age_seconds)

    stale_guilds = Guild.objects.filter(
        is_active=True,
        calendar_url__gt="",
    ).exclude(calendar_last_fetched_at__gte=cutoff)

    for guild in stale_guilds:
        try:
            sync_guild_calendar(guild)
        except Exception:  # noqa: BLE001
            pass

    stale_feeds = CalendarFeed.objects.filter(ical_url__gt="").exclude(last_fetched_at__gte=cutoff)
    for feed in stale_feeds:
        try:
            sync_calendar_feed(feed)
        except Exception:  # noqa: BLE001
            pass

    config = SiteConfiguration.load()
    if config.sync_classes_enabled:
        classes_stale = config.classes_last_synced_at is None or config.classes_last_synced_at < cutoff
        if classes_stale:
            try:
                sync_classes_calendar()
            except Exception:  # noqa: BLE001
                pass

    # Always publish local plfog classes to the calendar — independent of the
    # external Drupal feed toggle. Cheap (single join, no network).
    try:
        sync_local_class_events()
    except Exception:  # noqa: BLE001
        pass
