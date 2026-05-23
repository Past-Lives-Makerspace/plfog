"""BDD specs for CalendarEvent model and Guild calendar fields."""

from __future__ import annotations

import pytest

from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def describe_Guild_calendar_fields():
    def it_has_default_calendar_color():
        guild = GuildFactory()
        assert guild.calendar_color == "#4B9FEE"

    def it_stores_calendar_url():
        guild = GuildFactory(
            calendar_url="https://calendar.google.com/calendar/ical/test%40group.calendar.google.com/public/basic.ics"
        )
        assert "calendar.google.com" in guild.calendar_url

    def it_calendar_last_fetched_at_defaults_to_null():
        guild = GuildFactory()
        assert guild.calendar_last_fetched_at is None


def describe_SiteConfiguration_calendar_fields():
    def it_has_default_general_calendar_color():
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        assert config.general_calendar_color == "#EEB44B"

    def it_has_default_classes_calendar_color():
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        assert config.classes_calendar_color == "#7C5CBF"

    def it_general_calendar_url_defaults_to_blank():
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        assert config.general_calendar_url == ""

    def it_general_calendar_last_fetched_at_defaults_to_null():
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        assert config.general_calendar_last_fetched_at is None


def describe_CalendarEvent():
    def it_can_be_created_for_a_guild():
        from membership.models import CalendarEvent
        from django.utils import timezone

        guild = GuildFactory()
        now = timezone.now()
        event = CalendarEvent.objects.create(
            guild=guild,
            uid="abc123@google.com",
            title="Open Studio",
            start_dt=now,
            end_dt=now,
            fetched_at=now,
        )
        assert event.guild == guild
        assert str(event) == "Open Studio"

    def it_can_be_created_without_a_guild_for_general_events():
        from membership.models import CalendarEvent
        from django.utils import timezone

        now = timezone.now()
        event = CalendarEvent.objects.create(
            guild=None,
            uid="general-123@google.com",
            title="Potluck Night",
            start_dt=now,
            end_dt=now,
            fetched_at=now,
        )
        assert event.guild is None

    def it_orders_by_start_dt():
        from membership.models import CalendarEvent
        from django.utils import timezone
        from datetime import timedelta

        now = timezone.now()
        guild = GuildFactory()
        CalendarEvent.objects.create(
            guild=guild,
            uid="later",
            title="B",
            start_dt=now + timedelta(hours=2),
            end_dt=now + timedelta(hours=3),
            fetched_at=now,
        )
        CalendarEvent.objects.create(
            guild=guild,
            uid="earlier",
            title="A",
            start_dt=now,
            end_dt=now + timedelta(hours=1),
            fetched_at=now,
        )
        titles = list(CalendarEvent.objects.values_list("title", flat=True))
        assert titles == ["A", "B"]

    def describe_source_key():
        def it_returns_guild_id_string_for_guild_events():
            from django.utils import timezone
            from membership.models import CalendarEvent

            guild = GuildFactory()
            now = timezone.now()
            event = CalendarEvent.objects.create(
                guild=guild,
                source=CalendarEvent.Source.GUILD,
                uid="guild-key-test",
                title="Guild Event",
                start_dt=now,
                end_dt=now,
                fetched_at=now,
            )
            assert event.source_key == str(guild.pk)

        def it_returns_general_for_general_events():
            from django.utils import timezone
            from membership.models import CalendarEvent

            now = timezone.now()
            event = CalendarEvent.objects.create(
                guild=None,
                source=CalendarEvent.Source.GENERAL,
                uid="general-key-test",
                title="General Event",
                start_dt=now,
                end_dt=now,
                fetched_at=now,
            )
            assert event.source_key == "general"

        def it_returns_classes_for_classes_events():
            from django.utils import timezone
            from membership.models import CalendarEvent

            now = timezone.now()
            event = CalendarEvent.objects.create(
                guild=None,
                source=CalendarEvent.Source.CLASSES,
                uid="classes-key-test",
                title="Classes Event",
                start_dt=now,
                end_dt=now,
                fetched_at=now,
            )
            assert event.source_key == "classes"

        def it_returns_feed_pk_for_general_events_linked_to_a_feed():
            from django.utils import timezone

            from core.models import CalendarFeed
            from membership.models import CalendarEvent

            now = timezone.now()
            feed = CalendarFeed.objects.create(
                name="Workshops", ical_url="https://example.com/workshops.ics", color="#FF8800"
            )
            event = CalendarEvent.objects.create(
                guild=None,
                feed=feed,
                source=CalendarEvent.Source.GENERAL,
                uid="feed-key-test",
                title="Feed Event",
                start_dt=now,
                end_dt=now,
                fetched_at=now,
            )
            assert event.source_key == f"feed-{feed.pk}"


def describe_CalendarFeed():
    def it_orders_by_sort_order_then_pk():
        from core.models import CalendarFeed

        b = CalendarFeed.objects.create(name="B", ical_url="https://example.com/b.ics", sort_order=2)
        a = CalendarFeed.objects.create(name="A", ical_url="https://example.com/a.ics", sort_order=1)
        names = list(CalendarFeed.objects.values_list("name", flat=True))
        assert names == [a.name, b.name]

    def it_has_default_color():
        from core.models import CalendarFeed

        feed = CalendarFeed.objects.create(name="Default", ical_url="https://example.com/default.ics")
        assert feed.color == "#EEB44B"

    def it_str_returns_name():
        from core.models import CalendarFeed

        feed = CalendarFeed.objects.create(name="General Calendar", ical_url="https://example.com/g.ics")
        assert str(feed) == "General Calendar"

    def describe_upcoming():
        def it_returns_events_whose_end_time_is_in_the_future():
            from membership.models import CalendarEvent
            from datetime import timedelta
            from django.utils import timezone

            now = timezone.now()
            guild = GuildFactory()
            CalendarEvent.objects.create(
                guild=guild,
                uid="past",
                title="Past",
                start_dt=now - timedelta(days=1),
                end_dt=now - timedelta(hours=23),
                fetched_at=now,
            )
            CalendarEvent.objects.create(
                guild=guild,
                uid="future",
                title="Future",
                start_dt=now + timedelta(days=1),
                end_dt=now + timedelta(days=1, hours=1),
                fetched_at=now,
            )
            results = list(CalendarEvent.objects.upcoming())
            assert len(results) == 1
            assert results[0].title == "Future"
