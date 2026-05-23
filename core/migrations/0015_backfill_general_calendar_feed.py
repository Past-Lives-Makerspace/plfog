"""Backfill the legacy ``general_calendar_url`` on ``SiteConfiguration`` into a ``CalendarFeed`` row.

Runs once per environment. If no general calendar was ever configured, this is a no-op.
The reverse migration removes any feed named "General Calendar" — symmetric with the
forward step (it never removes a row this migration didn't create).
"""

from __future__ import annotations

from django.db import migrations

_FEED_NAME = "General Calendar"


def forwards(apps, schema_editor):
    SiteConfiguration = apps.get_model("core", "SiteConfiguration")
    CalendarFeed = apps.get_model("core", "CalendarFeed")
    CalendarEvent = apps.get_model("membership", "CalendarEvent")

    config = SiteConfiguration.objects.filter(pk=1).first()
    if config is None or not config.general_calendar_url:
        return

    feed, _created = CalendarFeed.objects.get_or_create(
        name=_FEED_NAME,
        defaults={
            "ical_url": config.general_calendar_url,
            "color": config.general_calendar_color or "#EEB44B",
            "last_fetched_at": config.general_calendar_last_fetched_at,
            "sort_order": 0,
        },
    )

    CalendarEvent.objects.filter(source="general", feed__isnull=True).update(feed=feed)


def backwards(apps, schema_editor):
    SiteConfiguration = apps.get_model("core", "SiteConfiguration")
    CalendarFeed = apps.get_model("core", "CalendarFeed")

    feed = CalendarFeed.objects.filter(name=_FEED_NAME).first()
    if feed is None:
        return

    config = SiteConfiguration.objects.filter(pk=1).first()
    if config is not None:
        config.general_calendar_url = feed.ical_url
        config.general_calendar_color = feed.color
        config.general_calendar_last_fetched_at = feed.last_fetched_at
        config.save(
            update_fields=[
                "general_calendar_url",
                "general_calendar_color",
                "general_calendar_last_fetched_at",
            ]
        )

    feed.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_calendarfeed"),
        ("membership", "0030_calendarevent_feed"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
