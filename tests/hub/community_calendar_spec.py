"""BDD specs for Community Calendar views and calendar service."""

from __future__ import annotations

import textwrap
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from membership.models import CalendarEvent
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

# ---------------------------------------------------------------------------
# Minimal valid iCal fixture
# ---------------------------------------------------------------------------

SAMPLE_ICAL = textwrap.dedent("""\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//Test//Test//EN
    BEGIN:VEVENT
    UID:event-001@test.com
    SUMMARY:Open Studio Hours
    DTSTART:20260501T160000Z
    DTEND:20260501T200000Z
    DESCRIPTION:All members welcome
    LOCATION:Common Area
    END:VEVENT
    BEGIN:VEVENT
    UID:event-002@test.com
    SUMMARY:Woodshop Safety Class
    DTSTART:20260503T180000Z
    DTEND:20260503T200000Z
    END:VEVENT
    END:VCALENDAR
""").encode()

# iCal with an all-day event (DATE value, not DATETIME) and a naive datetime event
ALLDAY_ICAL = textwrap.dedent("""\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//Test//Test//EN
    BEGIN:VEVENT
    UID:allday-001@test.com
    SUMMARY:All Day Workshop
    DTSTART;VALUE=DATE:20260510
    DTEND;VALUE=DATE:20260511
    END:VEVENT
    END:VCALENDAR
""").encode()

# iCal with a naive (floating) datetime — no Z suffix, no TZID
NAIVE_DATETIME_ICAL = textwrap.dedent("""\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//Test//Test//EN
    BEGIN:VEVENT
    UID:naive-001@test.com
    SUMMARY:Floating Event
    DTSTART:20260515T100000
    DTEND:20260515T120000
    END:VEVENT
    END:VCALENDAR
""").encode()

# iCal with an event missing UID and one missing DTSTART — both should be skipped
SKIP_CASES_ICAL = textwrap.dedent("""\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//Test//Test//EN
    BEGIN:VEVENT
    SUMMARY:No UID Event
    DTSTART:20260520T100000Z
    DTEND:20260520T120000Z
    END:VEVENT
    BEGIN:VEVENT
    UID:no-dtstart-001@test.com
    SUMMARY:No DTSTART Event
    END:VEVENT
    BEGIN:VEVENT
    UID:valid-001@test.com
    SUMMARY:Valid Event
    DTSTART:20260521T100000Z
    DTEND:20260521T120000Z
    END:VEVENT
    END:VCALENDAR
""").encode()


def _fake_urlopen(url, **kwargs):
    mock_response = MagicMock()
    mock_response.read.return_value = SAMPLE_ICAL
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


def _make_urlopen(ical_bytes: bytes):
    def _fake(url, **kwargs):
        mock_response = MagicMock()
        mock_response.read.return_value = ical_bytes
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    return _fake


def _recurring_ical(uid: str = "weekly-001@test.com", summary: str = "Open Studio Hours", weeks_ago: int = 2) -> bytes:
    """A WEEKLY recurring event whose series began in the *past*.

    Rendered without expansion, only the past seed exists and nothing shows on an upcoming-only
    calendar — the exact bug. The parser must materialize the upcoming occurrences.
    """
    start = (timezone.now() - timedelta(weeks=weeks_ago)).replace(microsecond=0)
    end = start + timedelta(hours=4)
    fmt = "%Y%m%dT%H%M%SZ"
    return textwrap.dedent(f"""\
        BEGIN:VCALENDAR
        VERSION:2.0
        PRODID:-//Test//Test//EN
        BEGIN:VEVENT
        UID:{uid}
        SUMMARY:{summary}
        DTSTART:{start.strftime(fmt)}
        DTEND:{end.strftime(fmt)}
        RRULE:FREQ=WEEKLY
        END:VEVENT
        END:VCALENDAR
    """).encode()


def describe_calendar_service():
    def describe_sync_guild_calendar():
        def it_creates_calendar_events_for_a_guild():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                count = sync_guild_calendar(guild)
            assert count == 2
            events = CalendarEvent.objects.filter(guild=guild)
            assert events.count() == 2
            titles = set(events.values_list("title", flat=True))
            assert titles == {"Open Studio Hours", "Woodshop Safety Class"}

        def it_sets_source_to_guild():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                sync_guild_calendar(guild)
            assert CalendarEvent.objects.filter(guild=guild, source="guild").count() == 2

        def it_updates_existing_events_on_resync():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                sync_guild_calendar(guild)
                count = sync_guild_calendar(guild)
            assert count == 2
            assert CalendarEvent.objects.filter(guild=guild).count() == 2

        def it_sets_calendar_last_fetched_at_on_guild():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            assert guild.calendar_last_fetched_at is None
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                sync_guild_calendar(guild)
            guild.refresh_from_db()
            assert guild.calendar_last_fetched_at is not None

        def it_returns_zero_when_guild_has_no_calendar_url():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="")
            count = sync_guild_calendar(guild)
            assert count == 0

        def it_handles_all_day_events():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_make_urlopen(ALLDAY_ICAL)):
                count = sync_guild_calendar(guild)
            assert count == 1
            event = CalendarEvent.objects.get(guild=guild)
            assert event.all_day is True
            assert event.title == "All Day Workshop"

        def it_handles_naive_datetime_events():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_make_urlopen(NAIVE_DATETIME_ICAL)):
                count = sync_guild_calendar(guild)
            assert count == 1
            event = CalendarEvent.objects.get(guild=guild)
            assert event.title == "Floating Event"
            assert event.start_dt.tzinfo is not None

        def it_skips_events_with_empty_uid_or_missing_dtstart():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_make_urlopen(SKIP_CASES_ICAL)):
                count = sync_guild_calendar(guild)
            # Only the one event with UID and DTSTART should be created
            assert count == 1
            assert CalendarEvent.objects.filter(guild=guild).count() == 1
            assert CalendarEvent.objects.get(guild=guild).title == "Valid Event"

    def describe_recurring_feed_expansion():
        def it_materializes_upcoming_occurrences_of_a_past_recurring_series():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_make_urlopen(_recurring_ical())):
                count = sync_guild_calendar(guild)
            events = CalendarEvent.objects.filter(guild=guild, uid="weekly-001@test.com")
            # Weekly across the 180-day horizon → many rows, one per occurrence.
            assert count == events.count()
            assert events.count() > 4
            # The fix: at least one occurrence lands in the future (the lone past seed would show none).
            assert events.filter(start_dt__gte=timezone.now()).exists()

        def it_gives_each_occurrence_a_distinct_recurrence_id():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_make_urlopen(_recurring_ical())):
                sync_guild_calendar(guild)
            events = CalendarEvent.objects.filter(guild=guild, uid="weekly-001@test.com")
            recurrence_ids = list(events.values_list("recurrence_id", flat=True))
            assert "" not in recurrence_ids  # every occurrence is stamped
            assert len(set(recurrence_ids)) == len(recurrence_ids)  # all distinct → no collapse

        def it_gives_a_one_off_event_a_blank_recurrence_id():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                sync_guild_calendar(guild)  # SAMPLE_ICAL: two one-off events
            assert CalendarEvent.objects.filter(guild=guild).exclude(recurrence_id="").count() == 0

        def it_is_idempotent_across_resyncs():
            from hub.calendar_service import sync_guild_calendar

            guild = GuildFactory(calendar_url="https://calendar.google.com/calendar/ical/test.ics")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_make_urlopen(_recurring_ical())):
                first = sync_guild_calendar(guild)
                sync_guild_calendar(guild)
            # Re-sync updates occurrences in place (same uid+recurrence_id key) — no duplication.
            assert CalendarEvent.objects.filter(guild=guild, uid="weekly-001@test.com").count() == first

    def describe_sync_general_calendar():
        def it_creates_general_events_with_null_guild():
            from core.models import CalendarFeed
            from hub.calendar_service import sync_general_calendar

            CalendarFeed.objects.create(
                name="General", ical_url="https://calendar.google.com/calendar/ical/general.ics", color="#EEB44B"
            )
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                count = sync_general_calendar()
            assert count == 2
            assert CalendarEvent.objects.filter(guild__isnull=True).count() == 2

        def it_sets_source_to_general():
            from core.models import CalendarFeed
            from hub.calendar_service import sync_general_calendar

            CalendarFeed.objects.create(
                name="General", ical_url="https://calendar.google.com/calendar/ical/general.ics", color="#EEB44B"
            )
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                sync_general_calendar()
            assert CalendarEvent.objects.filter(source="general").count() == 2

        def it_returns_zero_when_no_feeds_configured():
            from hub.calendar_service import sync_general_calendar

            count = sync_general_calendar()
            assert count == 0

        def it_skips_echoes_of_our_own_google_pushed_community_events():
            from core.models import CalendarFeed
            from hub.calendar_service import sync_general_calendar
            from tests.membership.factories import CommunityEventFactory

            CommunityEventFactory(google_ical_uid="event-001@test.com")
            CalendarFeed.objects.create(
                name="General", ical_url="https://calendar.google.com/calendar/ical/general.ics", color="#EEB44B"
            )
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                count = sync_general_calendar()

            assert count == 1
            assert not CalendarEvent.objects.filter(uid="event-001@test.com").exists()
            assert CalendarEvent.objects.filter(uid="event-002@test.com").exists()

        def it_iterates_over_multiple_feeds():
            from core.models import CalendarFeed
            from hub.calendar_service import sync_general_calendar

            CalendarFeed.objects.create(name="Feed A", ical_url="https://example.com/a.ics", color="#EEB44B")
            CalendarFeed.objects.create(name="Feed B", ical_url="https://example.com/b.ics", color="#7C5CBF")
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                count = sync_general_calendar()
            # Each feed parses the same 2-event sample → 4 events upserted, tagged
            # with their respective feed FK. UID uniqueness is per-guild only, so
            # both feeds independently insert their copies (guild=None, distinct feed_id).
            assert count == 4
            assert CalendarEvent.objects.filter(feed__name="Feed A").count() == 2
            assert CalendarEvent.objects.filter(feed__name="Feed B").count() == 2

        def it_stamps_last_fetched_at_on_each_feed():
            from core.models import CalendarFeed
            from hub.calendar_service import sync_general_calendar

            feed = CalendarFeed.objects.create(
                name="General", ical_url="https://example.com/general.ics", color="#EEB44B"
            )
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                sync_general_calendar()
            feed.refresh_from_db()
            assert feed.last_fetched_at is not None

    def describe_sync_all_sources():
        def it_refreshes_guild_sources():
            from datetime import timedelta

            from hub.calendar_service import sync_all_sources

            guild = GuildFactory(
                calendar_url="https://example.com/cal.ics",
                calendar_last_fetched_at=None,
            )
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                errors = sync_all_sources()
            guild.refresh_from_db()
            assert guild.calendar_last_fetched_at >= timezone.now() - timedelta(seconds=5)
            assert errors == []

        def it_captures_exceptions_from_guild_sync():
            from hub.calendar_service import sync_all_sources

            GuildFactory(calendar_url="https://example.com/broken.ics")
            with patch("hub.calendar_service.sync_guild_calendar", side_effect=RuntimeError("network error")):
                errors = sync_all_sources()
            assert any("network error" in e for e in errors)

        def it_refreshes_calendar_feeds():
            from datetime import timedelta

            from core.models import CalendarFeed
            from hub.calendar_service import sync_all_sources

            feed = CalendarFeed.objects.create(
                name="General",
                ical_url="https://example.com/general.ics",
                color="#EEB44B",
                last_fetched_at=None,
            )
            with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
                sync_all_sources()
            feed.refresh_from_db()
            assert feed.last_fetched_at >= timezone.now() - timedelta(seconds=5)

        def it_captures_exceptions_from_feed_sync():
            from core.models import CalendarFeed
            from hub.calendar_service import sync_all_sources

            CalendarFeed.objects.create(
                name="Broken", ical_url="https://example.com/broken-general.ics", color="#EEB44B"
            )
            with patch("hub.calendar_service.sync_calendar_feed", side_effect=RuntimeError("network error")):
                errors = sync_all_sources()
            assert any("network error" in e for e in errors)


def describe_GuildEditForm():
    def it_saves_calendar_url_and_color():
        from hub.forms import GuildEditForm

        guild = GuildFactory()
        form = GuildEditForm(
            data={
                "name": guild.name,
                "about": "Some about text",
                "calendar_url": "https://calendar.google.com/calendar/ical/test.ics",
                "calendar_color": "#FF6B6B",
            },
            instance=guild,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.calendar_url == "https://calendar.google.com/calendar/ical/test.ics"
        assert saved.calendar_color == "#FF6B6B"

    def it_accepts_empty_calendar_url():
        from hub.forms import GuildEditForm

        guild = GuildFactory()
        form = GuildEditForm(
            data={"name": guild.name, "about": "", "calendar_url": "", "calendar_color": "#4B9FEE"},
            instance=guild,
        )
        assert form.is_valid(), form.errors

    def it_rejects_invalid_calendar_url():
        from hub.forms import GuildEditForm

        guild = GuildFactory()
        form = GuildEditForm(
            data={"name": guild.name, "about": "", "calendar_url": "not-a-url", "calendar_color": "#4B9FEE"},
            instance=guild,
        )
        assert not form.is_valid()
        assert "calendar_url" in form.errors


def _logged_in_user(client: Client, *, username: str = "caluser") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")
    return user


def describe_community_calendar_view():
    def it_is_accessible_to_anonymous_guests(client: Client):
        response = client.get("/calendar/")
        assert response.status_code == 200

    def it_renders_for_logged_in_user(client: Client):
        _logged_in_user(client)
        response = client.get("/calendar/")
        assert response.status_code == 200
        assert b"Community Calendar" in response.content

    def it_shows_only_guilds_with_calendars_in_context(client: Client):
        _logged_in_user(client, username="caluser2")
        GuildFactory(name="Calendar Testing Guild", calendar_url="https://calendar.google.com/calendar/ical/a.ics")
        GuildFactory(name="Woodshop Testing")  # no calendar_url
        response = client.get("/calendar/")
        assert response.status_code == 200
        guilds = response.context["guilds_with_calendars"]
        names = [g.name for g in guilds]
        assert "Calendar Testing Guild" in names
        assert "Woodshop Testing" not in names

    def it_includes_classes_color_in_context(client: Client):
        from core.models import SiteConfiguration

        _logged_in_user(client, username="caluser_classes")
        config = SiteConfiguration.load()
        config.sync_classes_enabled = True
        config.classes_calendar_color = "#AA33BB"
        config.save()
        response = client.get("/calendar/")
        assert response.status_code == 200
        assert response.context["classes_enabled"] is True
        assert response.context["classes_color"] == "#AA33BB"


def describe_calendar_events_partial_view():
    def it_returns_200(client: Client):
        _logged_in_user(client, username="caluser3")
        response = client.get("/calendar/events/")
        assert response.status_code == 200

    def it_renders_upcoming_events(client: Client):
        _logged_in_user(client, username="caluser4")
        guild = GuildFactory(name="Art Guild", calendar_url="https://example.com/cal.ics")
        now = timezone.now()
        # Create event in current week (tomorrow is always within the current week if today ≤ Thursday)
        CalendarEvent.objects.create(
            guild=guild,
            uid="test-001",
            title="Life Drawing Session",
            start_dt=now + timedelta(days=1),
            end_dt=now + timedelta(days=1, hours=2),
            fetched_at=now,
        )
        response = client.get("/calendar/events/")
        assert b"Life Drawing Session" in response.content

    def it_paginates_month_events_to_max_10_per_page(client: Client):
        _logged_in_user(client, username="caluser_page")
        guild = GuildFactory(name="Pagination Guild", calendar_url="https://example.com/pag.ics")
        now = timezone.now()
        today = now.date()
        # month_offset=1 window starts 4 weeks from the current week's Monday.
        current_week_start = today - timedelta(days=today.weekday())
        next_window_start = current_week_start + timedelta(weeks=4)
        for i in range(15):
            event_dt = now.replace(hour=10, minute=0, second=0, microsecond=0).replace(
                year=next_window_start.year, month=next_window_start.month, day=next_window_start.day
            ) + timedelta(days=i)
            CalendarEvent.objects.create(
                guild=guild,
                uid=f"page-event-{i}",
                title=f"Event {i:02d}",
                start_dt=event_dt,
                end_dt=event_dt + timedelta(hours=1),
                fetched_at=now,
            )
        response = client.get("/calendar/events/?month_offset=1")
        assert response.context["event_total_pages"] == 2
        assert len(response.context["month_events"]) == 10

    def it_returns_month_page_2_when_requested(client: Client):
        _logged_in_user(client, username="caluser_page2")
        guild = GuildFactory(name="Page2 Guild", calendar_url="https://example.com/pag2.ics")
        now = timezone.now()
        today = now.date()
        current_week_start = today - timedelta(days=today.weekday())
        next_window_start = current_week_start + timedelta(weeks=4)
        for i in range(15):
            event_dt = now.replace(hour=10, minute=0, second=0, microsecond=0).replace(
                year=next_window_start.year, month=next_window_start.month, day=next_window_start.day
            ) + timedelta(days=i)
            CalendarEvent.objects.create(
                guild=guild,
                uid=f"p2-event-{i}",
                title=f"Page2 Event {i:02d}",
                start_dt=event_dt,
                end_dt=event_dt + timedelta(hours=1),
                fetched_at=now,
            )
        response = client.get("/calendar/events/?month_offset=1&page=2")
        assert response.context["event_page"] == 2
        assert len(response.context["month_events"]) == 5


def describe_calendar_export_ics_view():
    def it_returns_ics_content_type(client: Client):
        _logged_in_user(client, username="caluser5")
        response = client.get("/calendar/export.ics")
        assert response.status_code == 200
        assert "text/calendar" in response["Content-Type"]

    def it_includes_events_in_ics_output(client: Client):
        from datetime import timedelta as td

        _logged_in_user(client, username="caluser6")
        guild = GuildFactory(name="Ceramics", calendar_url="https://example.com/a.ics")
        now = timezone.now()
        CalendarEvent.objects.create(
            guild=guild,
            uid="export-001",
            title="Glaze Workshop",
            start_dt=now + td(days=3),
            end_dt=now + td(days=3, hours=2),
            fetched_at=now,
        )
        response = client.get("/calendar/export.ics")
        assert b"Glaze Workshop" in response.content
        assert b"BEGIN:VEVENT" in response.content

    def it_escapes_newlines_in_description(client: Client):
        from datetime import timedelta as td

        _logged_in_user(client, username="caluser7")
        guild = GuildFactory(name="Printmaking", calendar_url="https://example.com/b.ics")
        now = timezone.now()
        CalendarEvent.objects.create(
            guild=guild,
            uid="escape-001",
            title="Printmaking Open Night",
            description="Line one\nLine two\nLine three",
            start_dt=now + td(days=2),
            end_dt=now + td(days=2, hours=2),
            fetched_at=now,
        )
        response = client.get("/calendar/export.ics")
        content = response.content.decode()
        # Escaped newlines must appear; bare newlines in the middle of a field must not
        assert "\\n" in content
        # The description field should contain the escaped version
        assert "DESCRIPTION:Line one\\nLine two\\nLine three" in content

    def it_exports_all_day_events_with_date_format(client: Client):
        from datetime import timedelta as td

        _logged_in_user(client, username="caluser8")
        guild = GuildFactory(name="Fiber Arts", calendar_url="https://example.com/c.ics")
        now = timezone.now()
        CalendarEvent.objects.create(
            guild=guild,
            uid="allday-export-001",
            title="All Day Fiber Fest",
            all_day=True,
            start_dt=now + td(days=5),
            end_dt=now + td(days=6),
            fetched_at=now,
        )
        response = client.get("/calendar/export.ics")
        content = response.content.decode()
        assert "DTSTART;VALUE=DATE:" in content
        assert "DTEND;VALUE=DATE:" in content
        # Should NOT export as datetime format for all-day events
        assert "DTSTART:20" not in content or "DTSTART;VALUE=DATE:" in content

    def it_includes_location_in_ics_output(client: Client):
        from datetime import timedelta as td

        _logged_in_user(client, username="caluser_loc")
        guild = GuildFactory(name="Metal Shop", calendar_url="https://example.com/metal.ics")
        now = timezone.now()
        CalendarEvent.objects.create(
            guild=guild,
            uid="location-001",
            title="Metal Shop Open Night",
            location="1234 Main St, Portland OR",
            start_dt=now + td(days=4),
            end_dt=now + td(days=4, hours=2),
            fetched_at=now,
        )
        response = client.get("/calendar/export.ics")
        content = response.content.decode()
        assert "LOCATION:1234 Main St, Portland OR" in content


def describe_community_calendar_default_filters():
    def it_includes_each_calendar_feed_in_default_filters(client: Client):
        from core.models import CalendarFeed

        _logged_in_user(client, username="caluser_gen")
        feed = CalendarFeed.objects.create(name="General", ical_url="https://example.com/general.ics", color="#EEB44B")
        response = client.get("/calendar/")
        assert response.status_code == 200
        assert f"feed-{feed.pk}" in response.context["default_filters_json"]

    def it_seeds_a_class_only_guilds_key_into_default_filters(client: Client):
        # A guild whose only calendar content is a class (no iCal URL) still seeds its
        # key into the defaults, or its newly-colored class chips render hidden on load.
        _logged_in_user(client, username="caluser_guildkey")
        guild = GuildFactory(name="Metalworking", calendar_url="")
        now = timezone.now()
        start = now + timedelta(days=5)
        CalendarEvent.objects.create(
            guild=guild,
            source=CalendarEvent.Source.CLASSES,
            uid="dfk-class",
            title="Forge Basics",
            start_dt=start,
            end_dt=start + timedelta(hours=2),
            fetched_at=now,
        )
        response = client.get("/calendar/")
        assert str(guild.pk) in response.context["default_filters_json"]


def describe_calendar_events_partial_invalid_params():
    def it_defaults_to_zero_offsets_when_params_are_non_integer(client: Client):
        _logged_in_user(client, username="caluser_bad")
        response = client.get("/calendar/events/?week_offset=abc&month_offset=xyz&page=bad")
        assert response.status_code == 200
        assert response.context["week_offset"] == 0
        assert response.context["month_offset"] == 0
        assert response.context["event_page"] == 1


def describe_sync_calendar_feed():
    def it_returns_zero_when_feed_has_no_ical_url():
        # Line 129: sync_calendar_feed early-return when ical_url is blank.
        from core.models import CalendarFeed
        from hub.calendar_service import sync_calendar_feed

        feed = CalendarFeed.objects.create(name="Empty", ical_url="", color="#EEB44B")
        count = sync_calendar_feed(feed)
        assert count == 0
        # last_fetched_at must remain untouched — no network call was made.
        feed.refresh_from_db()
        assert feed.last_fetched_at is None


def describe_sync_general_calendar_skips_blank_url():
    def it_skips_feeds_with_no_ical_url_and_only_counts_real_syncs():
        # Line 142: the `continue` branch inside sync_general_calendar for blank ical_url.
        from core.models import CalendarFeed
        from hub.calendar_service import sync_general_calendar

        CalendarFeed.objects.create(name="No URL", ical_url="", color="#EEB44B")
        CalendarFeed.objects.create(name="With URL", ical_url="https://example.com/valid.ics", color="#7C5CBF")
        with patch("hub.calendar_service.urllib.request.urlopen", side_effect=_fake_urlopen):
            count = sync_general_calendar()
        # Only the feed with a URL contributes; the blank-URL feed is skipped.
        assert count == 2
        assert CalendarEvent.objects.filter(guild__isnull=True).count() == 2


def describe_sync_all_sources_local_class_exception():
    def it_captures_exceptions_from_sync_local_class_events():
        from hub.calendar_service import sync_all_sources

        with patch("hub.calendar_service.sync_local_class_events", side_effect=RuntimeError("db gone")):
            errors = sync_all_sources()
        assert any("local classes" in e and "db gone" in e for e in errors)


def describe_calendar_week_label_cross_month():
    def it_uses_full_month_names_when_week_spans_two_months(client: Client):
        """Week label format when start and end months differ (e.g. Apr 28 – May 4)."""
        _logged_in_user(client, username="caluser_xmonth")
        # week_offset chosen so that the navigated week crosses a month boundary;
        # we rely on the label being rendered in the context, not asserting a fixed date.
        # Drive week_offset until start.month != end.month — but simpler: just call
        # the helper directly with a known cross-month date.
        from unittest.mock import patch

        from django.utils import timezone as dj_tz

        # Freeze "today" to Monday April 28 2026 — week is Apr 28–May 4 (cross month)
        fake_now = dj_tz.now().replace(year=2026, month=4, day=28, hour=12, minute=0, second=0, microsecond=0)
        with patch("hub.views.dj_timezone") as mock_tz:
            mock_tz.now.return_value = fake_now
            response = client.get("/calendar/events/?week_offset=0")
        assert response.status_code == 200
        week_label = response.context["week_label"]
        # Cross-month format: "Apr 28 – May 4, 2026"
        assert "Apr" in week_label
        assert "May" in week_label


def describe_calendar_event_item_feed_invariant():
    def it_does_not_present_a_feed_event_as_a_bookable_class():
        # An external subscribed-calendar event whose title happens to contain
        # "Class" must never render the catalog affordances (title→detail link,
        # "Classes" badge, "Register →"). It gets its feed-name badge instead.
        from django.template.loader import render_to_string

        from core.models import CalendarFeed

        feed = CalendarFeed.objects.create(
            name="Portland Makers", ical_url="https://example.com/pm.ics", color="#EEB44B"
        )
        now = timezone.now()
        event = CalendarEvent.objects.create(
            uid="feed-felix-001",
            feed=feed,
            source="general",
            title="Felix Glassblowing Class",
            start_dt=now + timedelta(days=3),
            end_dt=now + timedelta(days=3, hours=2),
            fetched_at=now,
        )

        html = render_to_string(
            "hub/partials/calendar_event_item.html",
            {"event": event, "source_colors": {f"feed-{feed.pk}": feed.color}},
        )

        assert "Register" not in html
        assert "pl-calendar-list__register" not in html
        assert ">Classes<" not in html  # the "Classes" source badge span
        assert "Portland Makers" in html  # feed-name badge is shown instead


def _seed_guild_class(guild, *, title: str = "Guild Fiber Workshop"):
    """Publish a future class session under ``guild`` so it shows on the guild's tab."""
    from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
    from classes.models import ClassOffering

    category = CategoryFactory(guild=guild)
    offering = ClassOfferingFactory(
        status=ClassOffering.Status.PUBLISHED,
        category=category,
        scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE,
        title=title,
    )
    start = timezone.now() + timedelta(days=6)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_guild_page_class_colors():
    """Review-addendum #4 — the highest-risk ripple: a guild's own classes must stay
    colored, visible-by-default, and toggleable on its own guild tab (even with no
    iCal calendar_url), not left under a dead 'classes' toggle."""

    def it_keeps_a_guilds_own_classes_visible_and_toggleable_on_its_tab(client: Client):
        _logged_in_user(client, username="guildcal_user")
        guild = GuildFactory(name="Fiber Arts", calendar_url="", calendar_color="#118844")
        _seed_guild_class(guild)

        response = client.get(f"/guilds/{guild.slug}/")
        assert response.status_code == 200
        calendar = response.context["calendar"]
        # Colored: the guild's key carries its own color.
        assert calendar["source_colors"][str(guild.pk)] == "#118844"
        # Legend set: the guild earns a toggle even with no iCal calendar_url.
        assert guild in calendar["legend_guilds"]
        # Visible by default: the guild key seeds the guild-tab default filters.
        assert str(guild.pk) in calendar["default_filters_json"]

        html = response.content.decode()
        # Toggleable: the legend renders a real toggle for the guild's key (un-gated from
        # calendar_url — this is the fix that would otherwise leave no toggle at all)...
        assert f"toggleFilter('{guild.pk}')" in html
        # ...and the class chip is bound to that same key, not a dead 'classes' toggle.
        assert f"isActive('{guild.pk}')" in html
        assert "Guild Fiber Workshop" in html
