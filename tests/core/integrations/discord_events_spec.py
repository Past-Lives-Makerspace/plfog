"""BDD specs for the Discord Scheduled Events push service.

All mocked — these NEVER hit Discord. ``push_community_event`` / ``remove_community_event``
run against a fake client (``from_settings`` patched); the real client's REST methods are
exercised through ``respx``.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, call, patch

import httpx
import pytest
import respx
from dateutil.rrule import MONTHLY, WEEKLY, rrule
from dateutil.rrule import weekday as rr_weekday
from django.core.files.base import ContentFile
from django.utils import timezone

from core.integrations import discord_events as de
from core.models import SiteConfiguration
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, tiny_png_bytes

SERVER_ID = "server123"
_EVENTS_URL = f"https://discord.com/api/v10/guilds/{SERVER_ID}/scheduled-events"


def _enable_config() -> SiteConfiguration:
    """Turn the admin runtime toggle on and set a Discord server id (the two config gates)."""
    config = SiteConfiguration.load()
    config.discord_events_sync_enabled = True
    config.discord_server_id = SERVER_ID
    config.save(update_fields=["discord_events_sync_enabled", "discord_server_id"])
    return config


def _fake_client(*, enabled: bool = True, server_id: str = SERVER_ID, **methods: MagicMock) -> MagicMock:
    client = MagicMock(spec=de.DiscordScheduledEventsClient)
    client.enabled = enabled
    client.server_id = server_id
    client.insert_event = methods.get("insert_event", MagicMock(return_value={"id": "evt1"}))
    client.update_event = methods.get("update_event", MagicMock(return_value={"id": "evt1"}))
    client.delete_event = methods.get("delete_event", MagicMock(return_value=None))
    return client


@pytest.mark.django_db
def describe_push_community_event():
    def describe_studio_hours():
        def it_is_an_idle_noop_and_never_calls_the_api():
            _enable_config()
            event = CommunityEventFactory(studio_hours=True)
            client = _fake_client()
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            assert event.discord_sync_state == CommunityEvent.SyncState.IDLE
            assert event.discord_sync_error == de.DiscordEventsConfig.STUDIO_HOURS
            client.insert_event.assert_not_called()
            client.update_event.assert_not_called()

    def describe_when_sync_is_disabled():
        def it_marks_pending_with_a_reason_and_makes_no_api_call():
            # Config toggle off (default) → from_settings() returns a disabled client and the
            # push self-gates to PENDING. (from_settings folding the toggle into `enabled` is
            # covered by describe_from_settings, so client.enabled is the single gate here.)
            event = CommunityEventFactory()
            client = _fake_client(enabled=False)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            assert event.discord_sync_state == CommunityEvent.SyncState.PENDING
            assert event.discord_sync_error == de.DiscordEventsConfig.SYNC_OFF
            client.insert_event.assert_not_called()

    def describe_insert():
        def it_inserts_and_stores_the_returned_id():
            _enable_config()
            event = CommunityEventFactory()
            insert = MagicMock(return_value={"id": "new123"})
            client = _fake_client(insert_event=insert)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            insert.assert_called_once()
            assert insert.call_args.args[0] == SERVER_ID
            assert event.discord_event_id == "new123"
            assert event.discord_sync_state == CommunityEvent.SyncState.SYNCED
            assert event.discord_synced_at is not None
            assert event.discord_sync_error == ""
            assert event.discord_pushed_occurrence is None
            client.update_event.assert_not_called()

    def describe_update():
        def it_updates_an_already_pushed_event_instead_of_inserting():
            _enable_config()
            event = CommunityEventFactory(discord_event_id="existing1")
            update = MagicMock(return_value={"id": "existing1"})
            client = _fake_client(update_event=update)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            update.assert_called_once()
            assert update.call_args.args[:2] == (SERVER_ID, "existing1")
            client.insert_event.assert_not_called()
            assert event.discord_sync_state == CommunityEvent.SyncState.SYNCED

    def describe_recurrence():
        def it_pushes_a_weekly_series_natively_without_a_pushed_occurrence():
            _enable_config()
            event = CommunityEventFactory(recurrence=CommunityEvent.Recurrence.WEEKLY)
            insert = MagicMock(return_value={"id": "wk1"})
            client = _fake_client(insert_event=insert)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            body = insert.call_args.args[1]
            assert body["recurrence_rule"]["frequency"] == 2
            # Discord hard-requires the rule's own start matching scheduled_start_time
            assert body["recurrence_rule"]["start"] == body["scheduled_start_time"]
            assert event.discord_pushed_occurrence is None

        def it_pushes_the_next_occurrence_for_an_unmappable_cadence():
            _enable_config()
            event = CommunityEventFactory(recurrence=CommunityEvent.Recurrence.EVERY_2_MONTHS)
            insert = MagicMock(return_value={"id": "occ1"})
            client = _fake_client(insert_event=insert)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            body = insert.call_args.args[1]
            assert "recurrence_rule" not in body  # a single instance, not a native series
            assert event.discord_event_id == "occ1"
            assert event.discord_pushed_occurrence is not None

        def it_marks_synced_without_an_event_when_no_occurrence_is_in_the_horizon():
            _enable_config()
            far = timezone.now() + timedelta(days=400)
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.YEARLY, starts_at=far, ends_at=far + timedelta(hours=2)
            )
            client = _fake_client()
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            assert event.discord_sync_state == CommunityEvent.SyncState.SYNCED
            assert event.discord_event_id == ""
            assert event.discord_pushed_occurrence is None
            client.insert_event.assert_not_called()

        def it_creates_a_fresh_event_when_a_single_occurrence_rolls_forward():
            _enable_config()
            past = timezone.now() - timedelta(days=1)
            anchor = timezone.now() - timedelta(days=40)
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.EVERY_2_MONTHS,
                starts_at=anchor,
                ends_at=anchor + timedelta(hours=2),
                discord_event_id="old1",
                discord_sync_state=CommunityEvent.SyncState.SYNCED,
                discord_pushed_occurrence=past,
            )
            insert = MagicMock(return_value={"id": "fresh2"})
            update = MagicMock(return_value={"id": "old1"})
            client = _fake_client(insert_event=insert, update_event=update)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            insert.assert_called_once()  # a completed event can't be PATCHed → fresh create
            update.assert_not_called()
            assert event.discord_event_id == "fresh2"
            assert event.discord_pushed_occurrence is not None
            assert event.discord_pushed_occurrence > past

        def it_deletes_the_old_native_series_when_an_event_becomes_a_single_occurrence():
            # A monthly series pushed as a rule (pushed occurrence None) that is unmappable
            # now: the wrong series must leave Discord before its replacement lands, or
            # members see both.
            _enable_config()
            start = timezone.make_aware(datetime(2026, 8, 7, 18, 0))
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=start,
                ends_at=start + timedelta(hours=4),
                discord_event_id="series1",
                discord_sync_state=CommunityEvent.SyncState.SYNCED,
                discord_pushed_occurrence=None,
            )
            delete = MagicMock()
            insert = MagicMock(return_value={"id": "single1"})
            client = _fake_client(insert_event=insert, delete_event=delete)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            delete.assert_called_once_with(SERVER_ID, "series1")
            insert.assert_called_once()
            client.update_event.assert_not_called()
            assert "recurrence_rule" not in insert.call_args.args[1]
            assert event.discord_event_id == "single1"
            assert event.discord_pushed_occurrence is not None

        def it_keeps_the_old_series_and_fails_when_its_delete_fails():
            # The id must survive a failed delete, or the wrong series is orphaned on Discord
            # with nothing recording which one. FAILED puts the row in the retry cron's set,
            # which runs the delete and the insert again next tick.
            _enable_config()
            start = timezone.make_aware(datetime(2026, 8, 7, 18, 0))
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=start,
                ends_at=start + timedelta(hours=4),
                discord_event_id="series1",
                discord_sync_state=CommunityEvent.SyncState.SYNCED,
                discord_pushed_occurrence=None,
            )
            delete = MagicMock(side_effect=de.DiscordEventsError("Discord API 429: rate limited"))
            client = _fake_client(delete_event=delete)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                event.push_to_discord()  # the model path, which saves what the cron reads
            client.insert_event.assert_not_called()
            assert event.discord_event_id == "series1"
            assert event.discord_sync_state == CommunityEvent.SyncState.FAILED
            assert "series1" in event.discord_sync_error
            assert event in CommunityEvent.objects.needs_discord_push()

        def it_treats_an_already_deleted_series_as_gone():
            _enable_config()
            start = timezone.make_aware(datetime(2026, 8, 7, 18, 0))
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=start,
                ends_at=start + timedelta(hours=4),
                discord_event_id="series1",
                discord_sync_state=CommunityEvent.SyncState.SYNCED,
                discord_pushed_occurrence=None,
            )
            delete = MagicMock(side_effect=de.DiscordEventsError("Discord API 404: Unknown Guild Scheduled Event"))
            insert = MagicMock(return_value={"id": "single1"})
            client = _fake_client(insert_event=insert, delete_event=delete)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            insert.assert_called_once()
            assert event.discord_event_id == "single1"
            assert event.discord_sync_state == CommunityEvent.SyncState.SYNCED

        def it_records_the_start_it_sent_discord():
            _enable_config()
            event = CommunityEventFactory(recurrence=CommunityEvent.Recurrence.WEEKLY)
            client = _fake_client()
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            assert event.discord_pushed_start == event.starts_at  # the anchor, still ahead

        def it_creates_a_fresh_event_when_the_remote_one_is_gone():
            # Cancelled by hand on Discord: the PATCH 404s. Failing every tick against an
            # event that no longer exists helps nobody; the id is dropped and a new one made.
            _enable_config()
            event = CommunityEventFactory(discord_event_id="gone1")
            update = MagicMock(side_effect=de.DiscordEventsError("Discord API 404: Unknown Guild Scheduled Event"))
            insert = MagicMock(return_value={"id": "fresh1"})
            client = _fake_client(insert_event=insert, update_event=update)
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            insert.assert_called_once()
            assert event.discord_event_id == "fresh1"
            assert event.discord_sync_state == CommunityEvent.SyncState.SYNCED

    def describe_failure():
        def it_marks_failed_with_a_truncated_error_and_never_raises():
            _enable_config()
            event = CommunityEventFactory()
            client = _fake_client(insert_event=MagicMock(side_effect=de.DiscordEventsError("x" * 900)))
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)  # must not raise
            assert event.discord_sync_state == CommunityEvent.SyncState.FAILED
            assert len(event.discord_sync_error) == 500
            assert event.discord_event_id == ""


@pytest.mark.django_db
def describe_remove_community_event():
    def it_deletes_when_an_id_is_present():
        _enable_config()
        event = CommunityEventFactory(discord_event_id="evt9")
        delete = MagicMock()
        client = _fake_client(delete_event=delete)
        with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
            de.remove_community_event(event)
        delete.assert_called_once_with(SERVER_ID, "evt9")

    def it_swallows_a_delete_error():
        _enable_config()
        event = CommunityEventFactory(discord_event_id="evt9")
        client = _fake_client(delete_event=MagicMock(side_effect=de.DiscordEventsError("gone")))
        with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
            de.remove_community_event(event)  # must not raise

    def it_is_a_noop_when_disabled():
        event = CommunityEventFactory(discord_event_id="evt9")
        client = _fake_client(enabled=False)
        with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
            de.remove_community_event(event)
        client.delete_event.assert_not_called()

    def it_is_a_noop_when_the_event_was_never_pushed():
        _enable_config()
        event = CommunityEventFactory()  # blank discord_event_id
        client = _fake_client()
        with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
            de.remove_community_event(event)
        client.delete_event.assert_not_called()


@pytest.mark.django_db
def describe__build_scheduled_event_body():
    def _event(**kwargs) -> CommunityEvent:
        defaults = dict(
            title="Forge Night",
            location="Bay 3",
            description="Bring a project.",
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=2),
        )
        defaults.update(kwargs)
        return CommunityEventFactory(**defaults)

    def it_builds_an_external_guild_only_body_with_required_fields():
        body = de._build_scheduled_event_body(_event())
        assert body["entity_type"] == 3  # EXTERNAL
        assert body["privacy_level"] == 2  # GUILD_ONLY
        assert "scheduled_start_time" in body
        assert "scheduled_end_time" in body  # required for EXTERNAL
        assert body["entity_metadata"]["location"] == "Bay 3"

    def it_falls_back_to_the_default_location_when_blank():
        body = de._build_scheduled_event_body(_event(location=""))
        assert body["entity_metadata"]["location"] == de.DEFAULT_LOCATION

    def it_uses_the_video_url_as_location_when_there_is_no_physical_location():
        body = de._build_scheduled_event_body(_event(location="", video_url="https://meet.google.com/abc-defg-hij"))
        assert body["entity_metadata"]["location"] == "https://meet.google.com/abc-defg-hij"

    def it_keeps_the_physical_location_when_both_are_set():
        body = de._build_scheduled_event_body(_event(location="Bay 3", video_url="https://meet.google.com/abc"))
        assert body["entity_metadata"]["location"] == "Bay 3"

    def it_truncates_a_long_video_url_used_as_location():
        body = de._build_scheduled_event_body(_event(location="", video_url="https://meet.google.com/" + "x" * 150))
        assert len(body["entity_metadata"]["location"]) == 100

    def it_ends_the_description_with_the_public_url():
        event = _event()
        body = de._build_scheduled_event_body(event)
        assert body["description"].startswith("Bring a project.")
        assert body["description"].endswith(event.public_url)

    def it_leads_the_description_with_join_online_when_video_url_is_set():
        event = _event(video_url="https://meet.google.com/abc-defg-hij")
        body = de._build_scheduled_event_body(event)
        assert body["description"].startswith("Join online: https://meet.google.com/abc-defg-hij")

    def it_omits_the_join_online_line_when_video_url_is_blank():
        event = _event(video_url="")
        body = de._build_scheduled_event_body(event)
        assert "Join online:" not in body["description"]

    def it_truncates_the_name_to_100_chars():
        body = de._build_scheduled_event_body(_event(title="x" * 150))
        assert len(body["name"]) == 100

    def it_truncates_the_description_to_1000_chars():
        body = de._build_scheduled_event_body(_event(description="y" * 2000))
        assert len(body["description"]) == 1000

    def it_starts_a_series_at_its_next_occurrence_once_the_anchor_has_passed():
        # Parallel Play: weekly, anchored Thu 2026-07-23 17:00 PDT. Every edit after that date
        # re-sent the anchor and Discord answered GUILD_SCHEDULED_EVENT_SCHEDULE_PAST (failing
        # on every retry tick until 2026-10-10). The start is the next occurrence, in the UTC
        # offset of its own date, and the rule is anchored there too.
        anchor = timezone.make_aware(datetime(2026, 7, 23, 17, 0))
        event = _event(
            recurrence=CommunityEvent.Recurrence.WEEKLY, starts_at=anchor, ends_at=anchor + timedelta(hours=3)
        )
        now = timezone.make_aware(datetime(2026, 11, 3, 12, 0))
        with patch("django.utils.timezone.now", return_value=now):
            body = de._build_scheduled_event_body(event)
        assert body["scheduled_start_time"] == "2026-11-05T17:00:00-08:00"
        assert body["scheduled_end_time"] == "2026-11-05T20:00:00-08:00"
        assert body["recurrence_rule"]["start"] == body["scheduled_start_time"]
        assert body["recurrence_rule"]["by_weekday"] == [4]  # Fri 01:00 UTC

    def it_starts_a_series_at_its_anchor_while_that_is_still_ahead():
        anchor = timezone.now() + timedelta(days=3)
        event = _event(
            recurrence=CommunityEvent.Recurrence.WEEKLY, starts_at=anchor, ends_at=anchor + timedelta(hours=1)
        )
        sent = datetime.fromisoformat(de._build_scheduled_event_body(event)["scheduled_start_time"])
        assert sent == anchor  # the same instant, carried in Portland's offset

    def it_omits_recurrence_for_a_one_off_event():
        body = de._build_scheduled_event_body(_event(recurrence=CommunityEvent.Recurrence.NONE))
        assert "recurrence_rule" not in body

    def it_pushes_a_given_occurrence_as_a_single_instance():
        event = _event(recurrence=CommunityEvent.Recurrence.EVERY_2_MONTHS)
        occ = timezone.now() + timedelta(days=10)
        body = de._build_scheduled_event_body(event, occurrence=occ)
        assert body["scheduled_start_time"] == occ.isoformat()
        assert "recurrence_rule" not in body

    def describe_the_cover_image():
        def it_carries_the_photo_as_a_data_uri():
            # Discord takes a cover as base64 bytes in the body, not as a URL, so the picture
            # itself has to travel — and it is the event's own photo, decoded byte for byte.
            event = _event()
            raw = tiny_png_bytes()
            event.photo.save("forge.png", ContentFile(raw), save=True)
            body = de._build_scheduled_event_body(event)
            header, _, encoded = body["image"].partition(",")
            assert header == "data:image/png;base64"
            assert base64.b64decode(encoded) == raw

        def it_omits_the_image_for_an_event_with_no_photo():
            assert "image" not in de._build_scheduled_event_body(_event())

        def it_carries_the_cover_on_an_update_too():
            # An edit PATCHes the same body builder, so a photo added after the first push
            # still reaches Discord's Events tab.
            event = _event(discord_event_id="evt1")
            event.photo.save("forge.png", ContentFile(tiny_png_bytes()), save=True)
            _enable_config()
            client = _fake_client()
            with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
                de.push_community_event(event)
            client.update_event.assert_called_once()
            assert client.update_event.call_args.args[2]["image"].startswith("data:image/png;base64,")
            assert event.discord_sync_state == CommunityEvent.SyncState.SYNCED

        def it_pushes_without_a_cover_when_the_photo_cannot_be_read():
            # Storage is a network call in production. A bucket that is down, or an object
            # that is gone, must cost the banner and nothing else.
            event = _event()
            event.photo.save("forge.png", ContentFile(tiny_png_bytes()), save=True)
            _enable_config()
            client = _fake_client()
            with (
                patch.object(type(event.photo), "open", side_effect=OSError("bucket down")),
                patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client),
            ):
                de.push_community_event(event)
            assert "image" not in client.insert_event.call_args.args[1]
            assert event.discord_sync_state == CommunityEvent.SyncState.SYNCED  # the event still synced

        def it_skips_a_photo_bigger_than_the_cover_cap():
            event = _event()
            event.photo.save("forge.png", ContentFile(tiny_png_bytes()), save=True)
            with patch.object(type(event.photo), "size", de._COVER_IMAGE_MAX_BYTES + 1):
                assert "image" not in de._build_scheduled_event_body(event)


@pytest.mark.django_db
def describe__recurrence_rule_for():
    def _event(recurrence: str, **kwargs: Any) -> CommunityEvent:
        return CommunityEventFactory(recurrence=recurrence, **kwargs)

    def _anchored(recurrence: str, year: int, month: int, day: int) -> CommunityEvent:
        """An event anchored to an explicit LOCAL calendar date.

        The rule a monthly event produces depends entirely on where its anchor sits in its
        own month, so the anchor must never be derived from ``timezone.now()`` — the factory
        default (``now + 7 days``) silently drifts across weekday ordinals and made these
        specs pass or fail depending on the day they were run.
        """
        start = timezone.make_aware(datetime(year, month, day, 12, 0))
        return _event(recurrence, starts_at=start, ends_at=start + timedelta(hours=1))

    def it_returns_none_for_a_one_off() -> None:
        assert de._recurrence_rule_for(_event(CommunityEvent.Recurrence.NONE), timezone.now()) is None

    def it_maps_weekly_to_a_single_weekday_rule() -> None:
        # Wed 2026-07-08; Discord's by_weekday uses Python's 0=Monday … 6=Sunday encoding.
        event = _anchored(CommunityEvent.Recurrence.WEEKLY, 2026, 7, 8)
        assert de._recurrence_rule_for(event, event.starts_at) == {"frequency": 2, "interval": 1, "by_weekday": [2]}

    def it_uses_the_utc_weekday_for_an_evening_event_that_crosses_the_utc_date_line() -> None:
        # Thu 2026-07-16 17:00 PDT == Fri 2026-07-17 00:00 UTC. Discord evaluates the rule
        # against the UTC scheduled_start_time it is sent, so the rule must say Friday (4).
        # Sending the local weekday (Thursday, 3) made Discord snap the series to Thursday
        # 00:00 UTC — Wednesday 5 PM in Portland — showing every weekly evening meeting a
        # day early (the live "Parallel Play on Wednesday" bug, 2026-07-28).
        start = timezone.make_aware(datetime(2026, 7, 16, 17, 0))
        event = _event(CommunityEvent.Recurrence.WEEKLY, starts_at=start, ends_at=start + timedelta(hours=3))
        assert de._recurrence_rule_for(event, event.starts_at) == {"frequency": 2, "interval": 1, "by_weekday": [4]}

    def it_takes_the_weekday_from_the_pushed_start_not_the_anchor() -> None:
        # A 4:30 PM Thursday series is Thursday 23:30 UTC in summer and Friday 00:30 UTC in
        # winter. The rule follows the instant being pushed, so the winter re-anchor sends
        # Friday (4) where the July anchor would have said Thursday (3).
        anchor = timezone.make_aware(datetime(2026, 7, 16, 16, 30))
        event = _event(CommunityEvent.Recurrence.WEEKLY, starts_at=anchor, ends_at=anchor + timedelta(hours=2))
        assert de._recurrence_rule_for(event, anchor)["by_weekday"] == [3]
        winter = timezone.make_aware(datetime(2026, 11, 19, 16, 30))
        assert de._recurrence_rule_for(event, winter)["by_weekday"] == [4]

    def it_returns_none_for_a_monthly_event_that_crosses_the_date_line_only_in_winter() -> None:
        # Fri 2026-07-10 16:30 PDT is 23:30 UTC the same day, but 16:30 PST is 00:30 UTC the
        # next day, so a rule that held in July would snap to the wrong week in November.
        start = timezone.make_aware(datetime(2026, 7, 10, 16, 30))
        event = _event(CommunityEvent.Recurrence.MONTHLY, starts_at=start, ends_at=start + timedelta(hours=1))
        assert de._recurrence_rule_for(event, start) is None
        assert not de.pushes_as_native_series(event)

    def it_returns_none_for_a_monthly_evening_event_that_crosses_the_utc_date_line() -> None:
        # Fri 2026-07-10 18:00 PDT == Sat 2026-07-11 01:00 UTC. Discord counts the nth weekday
        # on the UTC calendar, and no "nth Saturday in UTC" lands on the nth Friday in Portland
        # in every month (describe_what_discord_shows has the live case), so the series takes
        # the single-next-occurrence fallback instead of a rule.
        start = timezone.make_aware(datetime(2026, 7, 10, 18, 0))
        event = _event(CommunityEvent.Recurrence.MONTHLY, starts_at=start, ends_at=start + timedelta(hours=1))
        assert de._recurrence_rule_for(event, event.starts_at) is None
        assert not de.pushes_as_native_series(event)

    @pytest.mark.parametrize(
        ("day", "expected_n", "expected_weekday"),
        [
            (1, 1, 2),  # Wed 2026-07-01 — the 1st Wednesday
            (6, 1, 0),  # Mon 2026-07-06 — the 1st Monday (weekday 0)
            (8, 2, 2),  # Wed 2026-07-08 — the 2nd Wednesday
            (15, 3, 2),  # Wed 2026-07-15 — the 3rd Wednesday
            (22, 4, 2),  # Wed 2026-07-22 — the 4th Wednesday
            (29, 5, 2),  # Wed 2026-07-29 — the 5th Wednesday: model ordinal -1 → Discord n=5
        ],
    )
    def it_maps_monthly_to_an_nth_weekday_rule(day: int, expected_n: int, expected_weekday: int) -> None:
        event = _anchored(CommunityEvent.Recurrence.MONTHLY, 2026, 7, day)
        assert de._recurrence_rule_for(event, event.starts_at) == {
            "frequency": 1,
            "interval": 1,
            "by_n_weekday": [{"n": expected_n, "day": expected_weekday}],
        }

    def it_maps_a_last_weekday_ordinal_to_discord_5() -> None:
        # Wed 2026-07-29 is the 5th (and last) Wednesday — the model calls that ordinal -1,
        # but Discord documents by_n_weekday.n as an int in 1–5 with no negative "last"
        # form; the rule (computed from the UTC calendar day) must land on 5, never -1
        # (which would be a hard 400).
        event = _anchored(CommunityEvent.Recurrence.MONTHLY, 2026, 7, 29)
        assert event._occurrence_ordinal() == -1
        assert de._recurrence_rule_for(event, event.starts_at)["by_n_weekday"][0]["n"] == 5

    def it_maps_a_4th_weekday_that_is_also_the_months_last_to_discord_4() -> None:
        # The other side of "last": Sat 2026-02-28 is both the 4th AND the final Saturday of
        # February. Discord is told where the anchor sits (n=4), not "last" — so the series
        # keeps landing on the 4th Saturday in months that happen to have five.
        event = _anchored(CommunityEvent.Recurrence.MONTHLY, 2026, 2, 28)
        assert event._occurrence_ordinal() == 4
        assert de._recurrence_rule_for(event, event.starts_at)["by_n_weekday"][0]["n"] == 4

    def it_keeps_every_calendar_anchor_inside_discords_1_to_5_range() -> None:
        # Every day of a 31-day month — no calendar position may produce an n Discord rejects.
        for day in range(1, 32):
            event = _anchored(CommunityEvent.Recurrence.MONTHLY, 2026, 7, day)
            rule = de._recurrence_rule_for(event, event.starts_at)
            assert 1 <= rule["by_n_weekday"][0]["n"] <= 5

    @pytest.mark.parametrize(
        "recurrence",
        [
            CommunityEvent.Recurrence.SEMI_MONTHLY,
            CommunityEvent.Recurrence.EVERY_2_MONTHS,
            CommunityEvent.Recurrence.EVERY_3_MONTHS,
            CommunityEvent.Recurrence.EVERY_6_MONTHS,
            CommunityEvent.Recurrence.YEARLY,
        ],
    )
    def it_returns_none_for_the_unmappable_cadences(recurrence: str) -> None:
        event = _anchored(recurrence, 2026, 7, 29)
        assert de._recurrence_rule_for(event, event.starts_at) is None
        assert not de.pushes_as_native_series(event)


def _discord_series(body: dict[str, Any], count: int) -> list[datetime]:
    """What Discord shows for a pushed body: the single instance, or the rule expanded in UTC.

    Discord expands a ``recurrence_rule`` with dateutil-rrule semantics on the UTC calendar
    from the rule's own ``start`` (no time zone, no clock change). Calibrated below against the
    live series Discord computed for the First Friday Art Walk.
    """
    rule = body.get("recurrence_rule")
    if rule is None:
        return [datetime.fromisoformat(body["scheduled_start_time"]).astimezone(UTC)]
    freq = {de._FREQUENCY_MONTHLY: MONTHLY, de._FREQUENCY_WEEKLY: WEEKLY}[rule["frequency"]]
    if rule.get("by_n_weekday"):
        byweekday = [rr_weekday(d["day"])(d["n"]) for d in rule["by_n_weekday"]]
    else:
        byweekday = [rr_weekday(d) for d in rule["by_weekday"]]
    start = datetime.fromisoformat(rule["start"]).astimezone(UTC)
    return list(rrule(freq, interval=rule["interval"], byweekday=byweekday, dtstart=start, count=count))


def _local_dates(series: list[datetime]) -> list[str]:
    return [timezone.localtime(occ).strftime("%a %Y-%m-%d %H:%M") for occ in series]


def describe_what_discord_shows():
    def it_reproduces_the_series_discord_computed_for_the_first_friday_art_walk() -> None:
        # The rule Discord held for prod event 3 on 2026-10-09 (GET scheduled-events/<id>):
        # start 2026-08-08T01:00Z, monthly, by_n_weekday [{n: 2, day: 5}]. Discord reported
        # scheduled_start_time 2026-10-10T01:00Z as the live instance, and its client listed
        # Fri Nov 13, Fri Dec 11 and Fri Jan 8 2027 at 5:00 PM as the rest of the series: the
        # second Fridays, an hour early once the clocks change. This pins the simulator to it.
        body = {
            "scheduled_start_time": "2026-10-10T01:00:00+00:00",
            "recurrence_rule": {
                "start": "2026-08-08T01:00:00+00:00",
                "frequency": 1,
                "interval": 1,
                "by_n_weekday": [{"n": 2, "day": 5}],
            },
        }
        series = _discord_series(body, 6)
        assert series[2] == datetime(2026, 10, 10, 1, 0, tzinfo=UTC)
        assert _local_dates(series[3:]) == ["Fri 2026-11-13 17:00", "Fri 2026-12-11 17:00", "Fri 2027-01-08 17:00"]

    @pytest.mark.django_db
    def it_keeps_a_first_friday_evening_series_on_first_fridays() -> None:
        # Prod event 3 as stored: monthly, anchored Fri 2026-08-07 18:00 PDT (the first Friday
        # of August), four hours long. Pushed on 2026-10-09 and rolled forward by the cron
        # after each date passes, Discord must show the first Friday of each month: the single
        # next instance each time, never a rule that drifts to the second Friday.
        _enable_config()
        start = timezone.make_aware(datetime(2026, 8, 7, 18, 0))
        event = CommunityEventFactory(
            recurrence=CommunityEvent.Recurrence.MONTHLY, starts_at=start, ends_at=start + timedelta(hours=4)
        )
        insert = MagicMock(side_effect=[{"id": f"occ{i}"} for i in range(3)])
        client = _fake_client(insert_event=insert)
        now = timezone.make_aware(datetime(2026, 10, 9, 12, 0))
        shown: list[datetime] = []
        with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
            for _ in range(3):
                with patch("django.utils.timezone.now", return_value=now):
                    event.push_to_discord()
                shown.extend(_discord_series(insert.call_args.args[1], 6))
                now = event.discord_pushed_occurrence + timedelta(hours=5)  # the tick after it ends
                assert event in CommunityEvent.objects.needs_discord_rollforward(now)
        assert _local_dates(shown) == ["Fri 2026-11-06 18:00", "Fri 2026-12-04 18:00", "Fri 2027-01-01 18:00"]
        assert insert.call_count == 3
        client.update_event.assert_not_called()

    @pytest.mark.django_db
    def it_keeps_a_weekly_evening_series_at_its_local_time_across_the_clock_change() -> None:
        # Clay Play: weekly, Tuesday 6 PM. Pushed in October (01:00 UTC) Discord's series
        # reads 5 PM from Nov 1, because the rule sits at a fixed UTC instant. The cron's
        # re-anchor pass PATCHes the same event with the first November occurrence as its
        # start (02:00 UTC), and the series reads 6 PM again. Idempotent until March.
        _enable_config()
        anchor = timezone.make_aware(datetime(2026, 10, 6, 18, 0))
        event = CommunityEventFactory(
            recurrence=CommunityEvent.Recurrence.WEEKLY, starts_at=anchor, ends_at=anchor + timedelta(hours=2)
        )
        insert = MagicMock(return_value={"id": "clay"})
        update = MagicMock(return_value={"id": "clay"})
        client = _fake_client(insert_event=insert, update_event=update)
        with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
            with patch("django.utils.timezone.now", return_value=timezone.make_aware(datetime(2026, 10, 5, 9, 0))):
                event.push_to_discord()
                assert not de.series_needs_reanchor(event)
            october = _discord_series(insert.call_args.args[1], 5)
            assert _local_dates(october)[-2:] == ["Tue 2026-10-27 18:00", "Tue 2026-11-03 17:00"]  # the drift
            with patch("django.utils.timezone.now", return_value=timezone.make_aware(datetime(2026, 11, 2, 9, 0))):
                assert de.series_needs_reanchor(event)
                event.push_to_discord()
                assert not de.series_needs_reanchor(event)
            update.assert_called_once()
            assert update.call_args.args[1] == "clay"  # the same Discord event, Interested marks kept
            november = _discord_series(update.call_args.args[2], 3)
        assert _local_dates(november) == ["Tue 2026-11-03 18:00", "Tue 2026-11-10 18:00", "Tue 2026-11-17 18:00"]
        assert event.discord_pushed_start == timezone.make_aware(datetime(2026, 11, 3, 18, 0))


@pytest.mark.django_db
def describe_CommunityEvent_push_to_discord():
    def it_persists_the_sync_fields_after_a_successful_push():
        _enable_config()
        event = CommunityEventFactory()
        client = _fake_client(insert_event=MagicMock(return_value={"id": "saved1"}))
        with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
            event.push_to_discord()
        event.refresh_from_db()
        assert event.discord_event_id == "saved1"
        assert event.discord_sync_state == CommunityEvent.SyncState.SYNCED

    def it_persists_a_failed_state_without_raising():
        _enable_config()
        event = CommunityEventFactory()
        client = _fake_client(insert_event=MagicMock(side_effect=de.DiscordEventsError("nope")))
        with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
            event.push_to_discord()
        event.refresh_from_db()
        assert event.discord_sync_state == CommunityEvent.SyncState.FAILED

    def it_delegates_removal_to_the_service():
        _enable_config()
        event = CommunityEventFactory(discord_event_id="evt9")
        delete = MagicMock()
        client = _fake_client(delete_event=delete)
        with patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=client):
            event.remove_from_discord()
        delete.assert_called_once_with(SERVER_ID, "evt9")


def describe_DiscordScheduledEventsClient():
    def describe_from_settings():
        def it_is_disabled_when_the_toggle_is_off(db, settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            config = SiteConfiguration.load()
            config.discord_server_id = SERVER_ID
            config.discord_events_sync_enabled = False
            config.save(update_fields=["discord_server_id", "discord_events_sync_enabled"])
            assert de.DiscordScheduledEventsClient.from_settings().enabled is False

        def it_is_disabled_when_the_bot_token_is_blank(db, settings):
            settings.DISCORD_BOT_TOKEN = ""
            config = SiteConfiguration.load()
            config.discord_server_id = SERVER_ID
            config.discord_events_sync_enabled = True
            config.save(update_fields=["discord_server_id", "discord_events_sync_enabled"])
            assert de.DiscordScheduledEventsClient.from_settings().enabled is False

        def it_is_disabled_when_no_server_is_linked(db, settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            config = SiteConfiguration.load()
            config.discord_server_id = ""
            config.discord_events_sync_enabled = True
            config.save(update_fields=["discord_server_id", "discord_events_sync_enabled"])
            assert de.DiscordScheduledEventsClient.from_settings().enabled is False

        def it_is_enabled_when_token_server_and_toggle_are_all_set(db, settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            config = SiteConfiguration.load()
            config.discord_server_id = SERVER_ID
            config.discord_events_sync_enabled = True
            config.save(update_fields=["discord_server_id", "discord_events_sync_enabled"])
            client = de.DiscordScheduledEventsClient.from_settings()
            assert client.enabled is True
            assert client.server_id == SERVER_ID

    def describe_api_methods():
        @respx.mock
        def it_inserts_via_post(settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            route = respx.post(_EVENTS_URL).mock(return_value=httpx.Response(200, json={"id": "e1"}))
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            assert client.insert_event(SERVER_ID, {"name": "x"}) == {"id": "e1"}
            assert route.called

        @respx.mock
        def it_updates_via_patch(settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            route = respx.patch(f"{_EVENTS_URL}/e1").mock(return_value=httpx.Response(200, json={"id": "e1"}))
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            assert client.update_event(SERVER_ID, "e1", {"name": "x"}) == {"id": "e1"}
            assert route.called

        @respx.mock
        def it_deletes_via_delete_returning_none(settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            respx.delete(f"{_EVENTS_URL}/e1").mock(return_value=httpx.Response(204))
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            assert client.delete_event(SERVER_ID, "e1") is None

        @respx.mock
        def it_wraps_a_non_2xx_as_discordeventserror(settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            respx.post(_EVENTS_URL).mock(return_value=httpx.Response(400, text="bad request"))
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            with pytest.raises(de.DiscordEventsError):
                client.insert_event(SERVER_ID, {})

        @respx.mock
        def it_wraps_a_network_error_as_discordeventserror(settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            respx.post(_EVENTS_URL).mock(side_effect=httpx.ConnectError("boom"))
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            with pytest.raises(de.DiscordEventsError):
                client.insert_event(SERVER_ID, {})

        @respx.mock
        def it_retries_once_after_a_rate_limits_advertised_wait(settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            route = respx.post(_EVENTS_URL).mock(
                side_effect=[
                    httpx.Response(429, json={"retry_after": 1.5}, headers={"Retry-After": "1.5"}),
                    httpx.Response(200, json={"id": "e1"}),
                ]
            )
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            with patch("core.integrations.discord_events.time.sleep") as fake_sleep:
                assert client.insert_event(SERVER_ID, {"name": "x"}, retry_on_rate_limit=True) == {"id": "e1"}
            fake_sleep.assert_called_once_with(1.5)
            assert route.call_count == 2

        @respx.mock
        def it_retries_up_to_the_max_attempts_before_succeeding(settings):
            # Two consecutive 429s then a 200 — the bounded retry keeps going past the first
            # retry (up to _RATE_LIMIT_MAX_ATTEMPTS - 1 waits) and still lands the send.
            settings.DISCORD_BOT_TOKEN = "tok"
            rate_limited = httpx.Response(429, json={"retry_after": 1.0}, headers={"Retry-After": "1.0"})
            route = respx.post(_EVENTS_URL).mock(
                side_effect=[rate_limited, rate_limited, httpx.Response(200, json={"id": "e1"})]
            )
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            with patch("core.integrations.discord_events.time.sleep") as fake_sleep:
                assert client.insert_event(SERVER_ID, {"name": "x"}, retry_on_rate_limit=True) == {"id": "e1"}
            assert route.call_count == 3
            assert fake_sleep.call_count == 2
            fake_sleep.assert_has_calls([call(1.0), call(1.0)])

        @respx.mock
        def it_fails_fast_on_a_429_without_the_retry_flag(settings):
            # Interactive FOG saves must not hang the request sleeping — only the
            # batch mirror opts into the wait-and-retry.
            settings.DISCORD_BOT_TOKEN = "tok"
            route = respx.post(_EVENTS_URL).mock(
                return_value=httpx.Response(429, json={"retry_after": 1.5}, headers={"Retry-After": "1.5"})
            )
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            with (
                patch("core.integrations.discord_events.time.sleep") as fake_sleep,
                pytest.raises(de.DiscordEventsError, match="429"),
            ):
                client.insert_event(SERVER_ID, {})
            fake_sleep.assert_not_called()
            assert route.call_count == 1

        @respx.mock
        def it_raises_after_exhausting_all_rate_limit_retries(settings):
            # Every attempt 429s — the bounded loop makes _RATE_LIMIT_MAX_ATTEMPTS calls
            # (the initial send + two retries) then gives up and raises.
            settings.DISCORD_BOT_TOKEN = "tok"
            rate_limited = httpx.Response(429, json={"retry_after": 1.0}, headers={"Retry-After": "1.0"})
            route = respx.post(_EVENTS_URL).mock(side_effect=[rate_limited, rate_limited, rate_limited])
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            with (
                patch("core.integrations.discord_events.time.sleep") as fake_sleep,
                pytest.raises(de.DiscordEventsError, match="429"),
            ):
                client.insert_event(SERVER_ID, {}, retry_on_rate_limit=True)
            assert route.call_count == 3
            assert fake_sleep.call_count == 2

        @respx.mock
        def it_raises_without_retrying_when_a_429_has_no_usable_retry_after(settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            route = respx.post(_EVENTS_URL).mock(return_value=httpx.Response(429, json={"global": True}))
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            with (
                patch("core.integrations.discord_events.time.sleep") as fake_sleep,
                pytest.raises(de.DiscordEventsError, match="429"),
            ):
                client.insert_event(SERVER_ID, {}, retry_on_rate_limit=True)
            fake_sleep.assert_not_called()
            assert route.call_count == 1

        @respx.mock
        def it_raises_without_retrying_when_the_retry_after_is_malformed(settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            route = respx.post(_EVENTS_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "soon"}))
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            with (
                patch("core.integrations.discord_events.time.sleep") as fake_sleep,
                pytest.raises(de.DiscordEventsError, match="429"),
            ):
                client.insert_event(SERVER_ID, {}, retry_on_rate_limit=True)
            fake_sleep.assert_not_called()
            assert route.call_count == 1

        @respx.mock
        def it_raises_without_retrying_when_the_wait_is_excessive(settings):
            settings.DISCORD_BOT_TOKEN = "tok"
            route = respx.post(_EVENTS_URL).mock(
                return_value=httpx.Response(429, json={"retry_after": 60.0}, headers={"Retry-After": "60"})
            )
            client = de.DiscordScheduledEventsClient(enabled=True, server_id=SERVER_ID)
            with (
                patch("core.integrations.discord_events.time.sleep") as fake_sleep,
                pytest.raises(de.DiscordEventsError, match="429"),
            ):
                client.insert_event(SERVER_ID, {}, retry_on_rate_limit=True)
            fake_sleep.assert_not_called()
            assert route.call_count == 1
