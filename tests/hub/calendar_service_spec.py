"""BDD specs for hub.calendar_service — sync_local_class_events and sync_all_sources."""

from __future__ import annotations

import itertools
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from core.integrations.discord_events import DiscordScheduledEventsClient
from core.models import SiteConfiguration
from membership.models import CalendarEvent

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# sync_local_class_events — the sole source of class events on the calendar
# ---------------------------------------------------------------------------


def _published_offering(**kwargs: object) -> ClassOffering:
    return ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, **kwargs)


def _future_session(offering: ClassOffering, days: int = 5) -> object:
    start = timezone.now() + timedelta(days=days)
    return ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))


def _session_at(offering: ClassOffering, days: int) -> object:
    """A session ``days`` from now (negative = already passed)."""
    start = timezone.now() + timedelta(days=days)
    return ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))


def _series(**kwargs: object) -> ClassOffering:
    return _published_offering(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE, **kwargs)


def describe_sync_local_class_events():
    def it_creates_a_calendar_event_per_upcoming_published_session():
        from hub.calendar_service import sync_local_class_events

        offering = _published_offering(title="Welding 101")
        session = _future_session(offering)

        count = sync_local_class_events()

        assert count == 1
        event = CalendarEvent.objects.get(source="classes", uid=f"local-class-{session.pk}")
        assert event.title == "Welding 101"
        assert event.start_dt == session.starts_at

    def it_purges_the_old_guild_copy_when_a_class_moves_guilds():
        from hub.calendar_service import sync_local_class_events
        from tests.membership.factories import GuildFactory

        offering = _published_offering()
        _future_session(offering)
        sync_local_class_events()
        assert CalendarEvent.objects.filter(source="classes", guild__isnull=True).count() == 1

        offering.category.guild = GuildFactory()
        offering.category.save()
        sync_local_class_events()

        events = CalendarEvent.objects.filter(source="classes")
        assert events.count() == 1
        assert events.get().guild == offering.category.guild

    def it_strips_cms_date_suffix_from_the_title():
        from hub.calendar_service import sync_local_class_events

        offering = _published_offering(title="Intro to Welding - 6/5/26")
        _future_session(offering)

        sync_local_class_events()

        event = CalendarEvent.objects.get(source="classes")
        assert event.title == "Intro to Welding"

    def it_links_to_the_local_class_page_not_the_legacy_site():
        from hub.calendar_service import sync_local_class_events

        offering = _published_offering(slug="welding-101")
        _future_session(offering)

        sync_local_class_events()

        event = CalendarEvent.objects.get(source="classes")
        assert event.url == "/classes/welding-101/"
        assert "pastlives.space" not in event.url

    def it_skips_draft_private_and_past_sessions():
        from hub.calendar_service import sync_local_class_events

        _future_session(ClassOfferingFactory(status=ClassOffering.Status.DRAFT))
        _future_session(_published_offering(is_private=True))
        past_offering = _published_offering()
        past = timezone.now() - timedelta(days=2)
        ClassSessionFactory(class_offering=past_offering, starts_at=past, ends_at=past + timedelta(hours=1))

        count = sync_local_class_events()

        assert count == 0
        assert CalendarEvent.objects.filter(source="classes").count() == 0

    def it_drops_a_started_multi_session_series():
        # A series drops out of the catalog the instant its first session begins;
        # the calendar must do the same — none of the still-future sessions 2–4
        # may materialize, or the member hits a dead-end (can't register).
        from hub.calendar_service import sync_local_class_events

        series = _series(title="Glassblowing Series")
        _session_at(series, -3)
        _session_at(series, 4)
        _session_at(series, 11)

        count = sync_local_class_events()

        assert count == 0
        assert CalendarEvent.objects.filter(source="classes").count() == 0

    def it_keeps_a_not_yet_started_series():
        from hub.calendar_service import sync_local_class_events

        series = _series()
        _session_at(series, 2)
        _session_at(series, 9)
        _session_at(series, 16)

        count = sync_local_class_events()

        assert count == 3
        assert CalendarEvent.objects.filter(source="classes").count() == 3

    def it_keeps_future_single_dates_when_a_sibling_date_has_passed():
        # Two single-session offerings sharing a catalog card (same title +
        # category → same grouping_key). The passed date drops; the future date
        # stays — per-offering gating, singles unharmed.
        from classes.factories import CategoryFactory
        from hub.calendar_service import sync_local_class_events

        category = CategoryFactory()
        past = _published_offering(title="Intro to Glass", category=category, slug="intro-glass-past")
        _session_at(past, -2)
        future = _published_offering(title="Intro to Glass", category=category, slug="intro-glass-future")
        future_session = _session_at(future, 6)

        assert past.grouping_key == future.grouping_key  # they collapse into one catalog card

        count = sync_local_class_events()

        assert count == 1
        event = CalendarEvent.objects.get(source="classes")
        assert event.uid == f"local-class-{future_session.pk}"

    def it_purges_leftover_legacy_drupal_calendar_events():
        from hub.calendar_service import sync_local_class_events

        CalendarEvent.objects.create(
            guild=None,
            uid="classes-node-123-0",
            source="classes",
            title="Legacy Welding",
            url="https://classes.pastlives.space/classes/welding-101",
            start_dt=timezone.now() + timedelta(days=3),
            end_dt=timezone.now() + timedelta(days=3, hours=2),
            fetched_at=timezone.now(),
        )

        sync_local_class_events()

        assert not CalendarEvent.objects.filter(uid="classes-node-123-0").exists()

    def it_purges_stale_local_events_when_a_session_is_removed():
        from hub.calendar_service import sync_local_class_events

        offering = _published_offering()
        session = _future_session(offering)
        sync_local_class_events()
        assert CalendarEvent.objects.filter(uid=f"local-class-{session.pk}").exists()

        session.delete()
        sync_local_class_events()

        assert not CalendarEvent.objects.filter(uid=f"local-class-{session.pk}").exists()

    def it_updates_an_existing_event_on_resync():
        from hub.calendar_service import sync_local_class_events

        offering = _published_offering(title="Original")
        session = _future_session(offering)
        sync_local_class_events()

        offering.title = "Updated"
        offering.save()
        sync_local_class_events()

        event = CalendarEvent.objects.get(uid=f"local-class-{session.pk}")
        assert event.title == "Updated"
        assert CalendarEvent.objects.filter(source="classes").count() == 1

    def it_sets_classes_last_synced_at():
        from hub.calendar_service import sync_local_class_events

        config = SiteConfiguration.load()
        config.classes_last_synced_at = None
        config.save()

        sync_local_class_events()

        config.refresh_from_db()
        assert config.classes_last_synced_at is not None


# ---------------------------------------------------------------------------
# Parity — the catalog and the calendar must agree about a started series
# ---------------------------------------------------------------------------


def describe_catalog_calendar_parity():
    def it_shows_a_series_on_both_catalog_and_calendar_before_it_starts():
        from hub.calendar_service import sync_local_class_events

        series = _series()
        _session_at(series, 2)
        _session_at(series, 9)

        sync_local_class_events()

        assert ClassOffering.objects.bookable().filter(pk=series.pk).exists()
        assert CalendarEvent.objects.filter(source="classes").exists()

    def it_hides_a_series_from_both_after_it_starts():
        from hub.calendar_service import sync_local_class_events

        series = _series()
        _session_at(series, -1)
        _session_at(series, 6)

        sync_local_class_events()

        assert not ClassOffering.objects.bookable().filter(pk=series.pk).exists()
        assert not CalendarEvent.objects.filter(source="classes").exists()


# ---------------------------------------------------------------------------
# sync_all_sources — legacy-CMS catalog importer branch
# ---------------------------------------------------------------------------


def describe_sync_all_sources_legacy_cms():
    def it_calls_sync_legacy_cms_when_enabled():
        from hub.calendar_service import sync_all_sources

        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = True
        config.save()

        with patch("classes.import_service.sync_legacy_cms") as mock_sync:
            errors = sync_all_sources()

        mock_sync.assert_called_once()
        assert errors == []

    def it_skips_sync_legacy_cms_when_disabled():
        from hub.calendar_service import sync_all_sources

        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = False
        config.save()

        with patch("classes.import_service.sync_legacy_cms") as mock_sync:
            sync_all_sources()

        mock_sync.assert_not_called()

    def it_captures_exceptions_from_legacy_cms_sync():
        from hub.calendar_service import sync_all_sources

        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = True
        config.save()

        with patch("classes.import_service.sync_legacy_cms", side_effect=RuntimeError("drupal down")):
            errors = sync_all_sources()

        assert any("legacy CMS" in e and "drupal down" in e for e in errors)


# ---------------------------------------------------------------------------
# sync_discord_feed_events — Discord Scheduled Events mirror of the general feeds
# ---------------------------------------------------------------------------

_uid_counter = itertools.count()


def _feed_event(days: float = 5, **kwargs: object) -> CalendarEvent:
    start = timezone.now() + timedelta(days=days)
    defaults: dict[str, object] = {
        "source": CalendarEvent.Source.GENERAL,
        "uid": f"feed-uid-{next(_uid_counter)}",
        "title": "Guild Meetup",
        "start_dt": start,
        "end_dt": start + timedelta(hours=1),
        "fetched_at": timezone.now(),
    }
    defaults.update(kwargs)
    return CalendarEvent.objects.create(**defaults)


def _events_client(enabled: bool = True) -> MagicMock:
    client = MagicMock(spec=DiscordScheduledEventsClient)
    client.enabled = enabled
    client.server_id = "srv1"
    client.insert_event.return_value = {"id": "disc-new"}
    return client


def _with_client(client: MagicMock):
    return patch.object(DiscordScheduledEventsClient, "from_settings", return_value=client)


def describe_sync_discord_feed_events():
    def it_is_a_noop_when_sync_is_disabled():
        from hub.calendar_service import sync_discord_feed_events

        _feed_event()
        client = _events_client(enabled=False)
        with _with_client(client):
            assert sync_discord_feed_events() == 0
        client.insert_event.assert_not_called()

    def it_creates_discord_events_and_stores_their_ids():
        from hub.calendar_service import sync_discord_feed_events

        event = _feed_event(location="", description="Bring gloves", url="https://example.com/e")
        client = _events_client()
        with _with_client(client):
            assert sync_discord_feed_events() == 1

        event.refresh_from_db()
        assert event.discord_event_id == "disc-new"
        body = client.insert_event.call_args.args[1]
        assert body["name"] == "Guild Meetup"
        assert body["entity_type"] == 3
        assert body["entity_metadata"] == {"location": "Past Lives Makerspace"}
        assert body["description"] == "Bring gloves\n\nhttps://example.com/e"
        assert body["scheduled_end_time"] == event.end_dt.isoformat()

    def it_updates_an_already_pushed_event():
        from hub.calendar_service import sync_discord_feed_events

        event = _feed_event(discord_event_id="disc-77")
        client = _events_client()
        with _with_client(client):
            sync_discord_feed_events()

        client.update_event.assert_called_once()
        assert client.update_event.call_args.args[1] == "disc-77"
        client.insert_event.assert_not_called()
        event.refresh_from_db()
        assert event.discord_event_id == "disc-77"

    def it_recreates_when_the_discord_copy_was_deleted_by_hand():
        from core.integrations.discord_events import DiscordEventsError
        from hub.calendar_service import sync_discord_feed_events

        event = _feed_event(discord_event_id="disc-gone")
        client = _events_client()
        client.update_event.side_effect = DiscordEventsError("Discord API 404: Unknown Guild Scheduled Event")
        with _with_client(client):
            sync_discord_feed_events()

        event.refresh_from_db()
        assert event.discord_event_id == "disc-new"

    def it_only_pushes_the_near_window():
        from hub.calendar_service import sync_discord_feed_events

        _feed_event(days=-1)
        _feed_event(days=40)
        client = _events_client()
        with _with_client(client):
            assert sync_discord_feed_events() == 0
        client.insert_event.assert_not_called()

    def it_caps_the_push_set():
        from hub.calendar_service import sync_discord_feed_events

        for _ in range(3):
            _feed_event()
        client = _events_client()
        with (
            _with_client(client),
            patch("hub.calendar_service._DISCORD_PUSH_CAP", 2),
            patch("hub.calendar_service.time.sleep"),
        ):
            assert sync_discord_feed_events() == 2
        assert client.insert_event.call_count == 2

    def it_deletes_the_copy_of_a_future_event_that_left_the_push_set():
        from hub.calendar_service import sync_discord_feed_events

        moved = _feed_event(days=40, discord_event_id="disc-moved")
        client = _events_client()
        with _with_client(client):
            sync_discord_feed_events()

        client.delete_event.assert_called_once_with("srv1", "disc-moved", retry_on_rate_limit=True)
        moved.refresh_from_db()
        assert moved.discord_event_id == ""

    def it_leaves_past_pushed_events_to_discords_own_lifecycle():
        from hub.calendar_service import sync_discord_feed_events

        past = _feed_event(days=-2, discord_event_id="disc-past")
        client = _events_client()
        with _with_client(client):
            sync_discord_feed_events()

        client.delete_event.assert_not_called()
        past.refresh_from_db()
        assert past.discord_event_id == "disc-past"

    def it_does_not_pace_a_single_push():
        # The very first Discord call of a run is un-paced — a lone event never sleeps.
        from hub.calendar_service import sync_discord_feed_events

        _feed_event()
        client = _events_client()
        with _with_client(client), patch("hub.calendar_service.time.sleep") as sleep:
            sync_discord_feed_events()
        sleep.assert_not_called()

    def it_paces_between_two_pushes():
        from hub.calendar_service import _DISCORD_PUSH_PACE_SECONDS, sync_discord_feed_events

        _feed_event(days=1)
        _feed_event(days=2)
        client = _events_client()
        with _with_client(client), patch("hub.calendar_service.time.sleep") as sleep:
            sync_discord_feed_events()
        sleep.assert_called_once_with(_DISCORD_PUSH_PACE_SECONDS)

    def it_paces_between_a_push_and_a_later_delete_in_the_same_run():
        # One shared counter across both loops: the push is call #1 (un-paced), the
        # later delete is call #2 and sleeps first. A per-loop counter would reset and
        # never sleep the delete — this pins that down.
        from hub.calendar_service import _DISCORD_PUSH_PACE_SECONDS, sync_discord_feed_events

        _feed_event(days=1)  # inside the 30-day window → pushed
        _feed_event(days=40, discord_event_id="disc-drop")  # future but out of window → dropped
        client = _events_client()
        with _with_client(client), patch("hub.calendar_service.time.sleep") as sleep:
            sync_discord_feed_events()
        sleep.assert_called_once_with(_DISCORD_PUSH_PACE_SECONDS)

    def it_attempts_every_event_then_raises_on_failures():
        from core.integrations.discord_events import DiscordEventsError
        from hub.calendar_service import sync_discord_feed_events

        _feed_event(days=1)
        second = _feed_event(days=2)
        client = _events_client()
        client.insert_event.side_effect = [DiscordEventsError("boom"), {"id": "disc-2"}]
        with (
            _with_client(client),
            patch("hub.calendar_service.time.sleep"),
            pytest.raises(DiscordEventsError, match="1 Discord event push"),
        ):
            sync_discord_feed_events()

        second.refresh_from_db()
        assert second.discord_event_id == "disc-2"

    def it_runs_inside_sync_all_sources_and_captures_failures():
        from hub.calendar_service import sync_all_sources

        with patch("hub.calendar_service.sync_discord_feed_events", side_effect=RuntimeError("discord down")):
            errors = sync_all_sources()

        assert any("discord events" in e and "discord down" in e for e in errors)

    def describe_echoes_of_our_own_google_pushed_events():
        def it_never_pushes_an_echo_and_purges_its_row():
            from hub.calendar_service import sync_discord_feed_events
            from tests.membership.factories import CommunityEventFactory

            CommunityEventFactory(google_ical_uid="echo-uid@google.com")
            _feed_event(uid="echo-uid@google.com")
            client = _events_client()
            with _with_client(client):
                assert sync_discord_feed_events() == 0

            client.insert_event.assert_not_called()
            assert not CalendarEvent.objects.filter(uid="echo-uid@google.com").exists()

        def it_deletes_the_discord_copy_an_echo_gained_before_suppression_existed():
            from hub.calendar_service import sync_discord_feed_events
            from tests.membership.factories import CommunityEventFactory

            CommunityEventFactory(google_ical_uid="echo-uid@google.com")
            _feed_event(uid="echo-uid@google.com", discord_event_id="disc-echo")
            client = _events_client()
            with _with_client(client):
                sync_discord_feed_events()

            client.delete_event.assert_called_once_with("srv1", "disc-echo", retry_on_rate_limit=True)
            assert not CalendarEvent.objects.filter(uid="echo-uid@google.com").exists()

        def it_purges_safe_echo_rows_even_while_discord_sync_is_off():
            from hub.calendar_service import sync_discord_feed_events
            from tests.membership.factories import CommunityEventFactory

            CommunityEventFactory(google_ical_uid="echo-uid@google.com")
            _feed_event(uid="echo-uid@google.com")
            client = _events_client(enabled=False)
            with _with_client(client):
                assert sync_discord_feed_events() == 0

            assert not CalendarEvent.objects.filter(uid="echo-uid@google.com").exists()

        def it_keeps_an_echo_row_whose_discord_delete_failed_for_a_retry_next_sweep():
            from core.integrations.discord_events import DiscordEventsError
            from hub.calendar_service import sync_discord_feed_events
            from tests.membership.factories import CommunityEventFactory

            CommunityEventFactory(google_ical_uid="echo-uid@google.com")
            echo = _feed_event(uid="echo-uid@google.com", discord_event_id="disc-echo")
            client = _events_client()
            client.delete_event.side_effect = DiscordEventsError("Discord API 500: oops")
            with _with_client(client), pytest.raises(DiscordEventsError):
                sync_discord_feed_events()

            echo.refresh_from_db()
            assert echo.discord_event_id == "disc-echo"
