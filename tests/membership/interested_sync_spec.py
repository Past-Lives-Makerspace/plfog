"""Specs for the Discord Interested → RSVP sync.

The reconcile is pure DB (source-scoped adds/removes); the sweep wires it to the
Scheduled-Events client (respx) and the announcement refresh. Button and hub RSVPs
must be untouchable by the sync — that is the whole safety contract.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from django.utils import timezone

from core.models import SiteConfiguration
from membership.interested_sync import sync_interested_rsvps
from membership.models import CommunityEvent, EventRSVP
from tests.membership.factories import CommunityEventFactory, MemberFactory

pytestmark = pytest.mark.django_db

_USERS_URL = "https://discord.com/api/v10/guilds/srv1/scheduled-events/dev42/users"


def _pushed_event(**overrides) -> CommunityEvent:
    starts = timezone.now() + timedelta(days=3)
    fields = {
        "moderation_state": CommunityEvent.ModerationState.PUBLISHED,
        "starts_at": starts,
        "ends_at": starts + timedelta(hours=1),
        "discord_event_id": "dev42",
    }
    fields.update(overrides)
    return CommunityEventFactory(**fields)


def _enable_sync(settings) -> None:
    settings.DISCORD_BOT_TOKEN = "bot"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv1"
    config.discord_events_sync_enabled = True
    config.save()


def describe_reconcile_interested():
    def it_adds_linked_members_with_the_interested_source():
        event = _pushed_event()
        member = MemberFactory(discord_user_id="111")
        assert event.reconcile_interested({"111"}) is True
        rsvp = event.rsvps.get(member=member)
        assert rsvp.source == EventRSVP.Source.INTERESTED

    def it_ignores_discord_ids_with_no_linked_member():
        event = _pushed_event()
        assert event.reconcile_interested({"999"}) is False
        assert not event.rsvps.exists()

    def it_leaves_an_existing_button_rsvp_alone():
        event = _pushed_event()
        member = MemberFactory(discord_user_id="111")
        event.toggle_rsvp(member)  # button-sourced
        assert event.reconcile_interested({"111"}) is False
        assert event.rsvps.get(member=member).source == EventRSVP.Source.BUTTON

    def it_removes_only_its_own_rows_when_interest_is_cleared():
        event = _pushed_event()
        interested = MemberFactory(discord_user_id="111")
        button = MemberFactory(discord_user_id="222")
        hub = MemberFactory(discord_user_id="333")
        event.reconcile_interested({"111"})
        event.toggle_rsvp(button)
        event.toggle_rsvp(hub, source="hub")

        assert event.reconcile_interested(set()) is True  # 111 cleared their bell
        remaining = set(event.rsvps.values_list("member__discord_user_id", flat=True))
        assert remaining == {"222", "333"}
        assert not event.rsvps.filter(member=interested).exists()

    def it_reports_no_change_when_the_list_matches():
        event = _pushed_event()
        MemberFactory(discord_user_id="111")
        event.reconcile_interested({"111"})
        assert event.reconcile_interested({"111"}) is False


def describe_sync_interested_rsvps():
    @respx.mock
    def it_sweeps_pushed_upcoming_events_and_refreshes_changed_ones(settings):
        _enable_sync(settings)
        event = _pushed_event()
        MemberFactory(discord_user_id="111")
        respx.get(url__startswith=_USERS_URL).mock(return_value=httpx.Response(200, json=[{"user": {"id": "111"}}]))
        with patch.object(CommunityEvent, "refresh_discord_announcement") as refresh:
            assert sync_interested_rsvps() == 1
        assert refresh.called
        assert event.rsvps.count() == 1

    @respx.mock
    def it_does_not_refresh_when_nothing_changed(settings):
        _enable_sync(settings)
        _pushed_event()
        respx.get(url__startswith=_USERS_URL).mock(return_value=httpx.Response(200, json=[]))
        with patch.object(CommunityEvent, "refresh_discord_announcement") as refresh:
            assert sync_interested_rsvps() == 0
        assert not refresh.called

    @respx.mock
    def it_skips_an_event_whose_discord_call_fails(settings):
        _enable_sync(settings)
        _pushed_event()
        respx.get(url__startswith=_USERS_URL).mock(return_value=httpx.Response(404, json={"message": "Unknown"}))
        assert sync_interested_rsvps() == 0  # logged and skipped, never raises

    def it_is_a_noop_when_the_client_is_disabled(settings):
        settings.DISCORD_BOT_TOKEN = ""
        _pushed_event()
        assert sync_interested_rsvps() == 0

    @respx.mock
    def it_ignores_unpushed_past_and_pending_events(settings):
        _enable_sync(settings)
        past = timezone.now() - timedelta(days=2)
        _pushed_event(discord_event_id="")  # never pushed
        _pushed_event(starts_at=past, ends_at=past + timedelta(hours=1))  # over, one-off
        _pushed_event(moderation_state=CommunityEvent.ModerationState.PENDING)
        route = respx.get(url__startswith=_USERS_URL).mock(return_value=httpx.Response(200, json=[]))
        assert sync_interested_rsvps() == 0
        assert not route.called

    @respx.mock
    def it_keeps_a_recurring_series_with_a_past_anchor(settings):
        _enable_sync(settings)
        past = timezone.now() - timedelta(days=2)
        _pushed_event(starts_at=past, ends_at=past + timedelta(hours=1), recurrence=CommunityEvent.Recurrence.WEEKLY)
        route = respx.get(url__startswith=_USERS_URL).mock(return_value=httpx.Response(200, json=[]))
        sync_interested_rsvps()
        assert route.called


def describe_list_interested_user_ids():
    @respx.mock
    def it_paginates_past_a_full_page(settings):
        from core.integrations.discord_events import DiscordScheduledEventsClient

        settings.DISCORD_BOT_TOKEN = "bot"
        client = DiscordScheduledEventsClient(enabled=True, server_id="srv1")
        first = [{"user": {"id": str(i)}} for i in range(100)]
        second = [{"user": {"id": "100"}}]
        route = respx.get(url__startswith=_USERS_URL).mock(
            side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
        )
        ids = client.list_interested_user_ids("srv1", "dev42")
        assert len(ids) == 101
        assert "after=99" in str(route.calls.last.request.url)


def describe_the_scheduled_job():
    def it_is_registered_on_the_fifteen_minute_dispatcher():
        from core.scheduled_jobs import JOBS_BY_KEY

        job = JOBS_BY_KEY["sync_interested_rsvps"]
        assert job.command == "sync_interested_rsvps"


def describe_source_stamps():
    def it_defaults_the_button_source_and_stamps_hub(settings):
        event = _pushed_event()
        button = MemberFactory(discord_user_id="1")
        hub = MemberFactory(discord_user_id="2")
        event.toggle_rsvp(button)
        event.toggle_rsvp(hub, source="hub")
        assert event.rsvps.get(member=button).source == EventRSVP.Source.BUTTON
        assert event.rsvps.get(member=hub).source == EventRSVP.Source.HUB
