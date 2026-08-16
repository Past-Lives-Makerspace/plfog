"""BDD specs for the Discord Scheduled Events push service.

All mocked — these NEVER hit Discord. ``push_community_event`` / ``remove_community_event``
run against a fake client (``from_settings`` patched); the real client's REST methods are
exercised through ``respx``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, call, patch

import httpx
import pytest
import respx
from django.utils import timezone

from core.integrations import discord_events as de
from core.models import SiteConfiguration
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory

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

    def it_omits_recurrence_for_a_one_off_event():
        body = de._build_scheduled_event_body(_event(recurrence=CommunityEvent.Recurrence.NONE))
        assert "recurrence_rule" not in body

    def it_pushes_a_given_occurrence_as_a_single_instance():
        event = _event(recurrence=CommunityEvent.Recurrence.EVERY_2_MONTHS)
        occ = timezone.now() + timedelta(days=10)
        body = de._build_scheduled_event_body(event, occurrence=occ)
        assert body["scheduled_start_time"] == occ.isoformat()
        assert "recurrence_rule" not in body


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
        assert de._recurrence_rule_for(_event(CommunityEvent.Recurrence.NONE)) is None

    def it_maps_weekly_to_a_single_weekday_rule() -> None:
        # Wed 2026-07-08; Discord's by_weekday uses Python's 0=Monday … 6=Sunday encoding.
        event = _anchored(CommunityEvent.Recurrence.WEEKLY, 2026, 7, 8)
        assert de._recurrence_rule_for(event) == {"frequency": 2, "interval": 1, "by_weekday": [2]}

    def it_uses_the_utc_weekday_for_an_evening_event_that_crosses_the_utc_date_line() -> None:
        # Thu 2026-07-16 17:00 PDT == Fri 2026-07-17 00:00 UTC. Discord evaluates the rule
        # against the UTC scheduled_start_time it is sent, so the rule must say Friday (4).
        # Sending the local weekday (Thursday, 3) made Discord snap the series to Thursday
        # 00:00 UTC — Wednesday 5 PM in Portland — showing every weekly evening meeting a
        # day early (the live "Parallel Play on Wednesday" bug, 2026-07-28).
        start = timezone.make_aware(datetime(2026, 7, 16, 17, 0))
        event = _event(CommunityEvent.Recurrence.WEEKLY, starts_at=start, ends_at=start + timedelta(hours=3))
        assert de._recurrence_rule_for(event) == {"frequency": 2, "interval": 1, "by_weekday": [4]}

    def it_uses_the_utc_calendar_day_for_a_monthly_evening_event_that_crosses_the_utc_date_line() -> None:
        # Fri 2026-07-10 18:00 PDT == Sat 2026-07-11 01:00 UTC — the 2nd Friday locally is
        # the 2nd Saturday in UTC. Both halves of the rule (n AND day) must come from the
        # same UTC instant, or Discord anchors the series to the wrong day.
        start = timezone.make_aware(datetime(2026, 7, 10, 18, 0))
        event = _event(CommunityEvent.Recurrence.MONTHLY, starts_at=start, ends_at=start + timedelta(hours=1))
        assert de._recurrence_rule_for(event) == {
            "frequency": 1,
            "interval": 1,
            "by_n_weekday": [{"n": 2, "day": 5}],
        }

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
        assert de._recurrence_rule_for(event) == {
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
        assert de._recurrence_rule_for(event)["by_n_weekday"][0]["n"] == 5

    def it_maps_a_4th_weekday_that_is_also_the_months_last_to_discord_4() -> None:
        # The other side of "last": Sat 2026-02-28 is both the 4th AND the final Saturday of
        # February. Discord is told where the anchor sits (n=4), not "last" — so the series
        # keeps landing on the 4th Saturday in months that happen to have five.
        event = _anchored(CommunityEvent.Recurrence.MONTHLY, 2026, 2, 28)
        assert event._occurrence_ordinal() == 4
        assert de._recurrence_rule_for(event)["by_n_weekday"][0]["n"] == 4

    def it_keeps_every_calendar_anchor_inside_discords_1_to_5_range() -> None:
        # Every day of a 31-day month — no calendar position may produce an n Discord rejects.
        for day in range(1, 32):
            rule = de._recurrence_rule_for(_anchored(CommunityEvent.Recurrence.MONTHLY, 2026, 7, day))
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
        assert de._recurrence_rule_for(_anchored(recurrence, 2026, 7, 29)) is None


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
