"""Specs for cancelling an event from its #calendar card's ⚙ Manage button (membership.discord_commands).

Covers who may cancel (``_can_cancel``), the confirm card's copy, Keep it, the confirmed
delete's Google + Discord unwind behind a type-6 ack (Discord REST mocked with ``respx``),
and every already-handled / authority-lost / malformed reply.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from django.utils import timezone

from membership.discord_commands import _can_cancel, _cancel_component, _event_component
from membership.models import CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db

_CALLBACK_URL = "https://discord.com/api/v10/interactions/intA/tokB/callback"
_FOLLOWUP_URL = "https://discord.com/api/v10/webhooks/appX/tokB/messages/@original"


def _discord_settings(settings) -> None:
    settings.DISCORD_BOT_TOKEN = "bot"
    settings.DISCORD_CLIENT_ID = "appX"
    settings.MEMBER_BASE_URL = "https://members.example"
    settings.DISCORD_NOTIFY_WEBHOOK_URL = ""


def _mock_discord() -> object:
    respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
    return respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))


def _published(**overrides) -> CommunityEvent:
    starts = timezone.now() + timedelta(days=3)
    fields = {
        "moderation_state": CommunityEvent.ModerationState.PUBLISHED,
        "guild": None,
        "event_type": CommunityEvent.EventType.COMMUNITY,
        "starts_at": starts,
        "ends_at": starts + timedelta(hours=1),
    }
    fields.update(overrides)
    return CommunityEventFactory(**fields)


def _led_guild(lead: Member, name: str = "Mine"):
    guild = GuildFactory(name=name)
    guild.guild_lead = lead
    guild.save(update_fields=["guild_lead"])
    return guild


def _confirm_card(member, pk) -> dict:
    return _event_component({"data": {"custom_id": f"event:cancelcard:{pk}"}}, member)


def _click(member, action: str, pk) -> dict:
    interaction = {"id": "intA", "token": "tokB", "data": {"custom_id": f"cancel:{action}:{pk}"}}
    return _cancel_component(interaction, member)


def describe_can_cancel():
    def it_lets_an_admin_cancel_any_published_or_scheduled_event(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        assert _can_cancel(admin, _published())
        assert _can_cancel(admin, _published(moderation_state=CommunityEvent.ModerationState.SCHEDULED))

    def it_lets_a_guild_lead_cancel_only_their_guilds_events(linked_member):
        lead = linked_member()
        assert _can_cancel(lead, _published(guild=_led_guild(lead)))
        assert not _can_cancel(lead, _published(guild=GuildFactory(name="Theirs")))
        assert not _can_cancel(lead, _published())  # site wide: admins only

    def it_refuses_an_event_that_is_not_live(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        assert not _can_cancel(admin, _published(moderation_state=CommunityEvent.ModerationState.PENDING))


def describe_the_confirm_card():
    def it_states_what_confirming_does(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published(title="Big Night")
        result = _confirm_card(admin, event.pk)
        assert result["type"] == 7  # in-place UPDATE_MESSAGE
        content = result["data"]["content"]
        assert "Cancel **Big Night**" in content
        assert "Google Calendar, and Discord" in content
        assert "repeating series" not in content
        buttons = result["data"]["components"][0]["components"]
        assert [b["custom_id"] for b in buttons] == [f"cancel:confirm:{event.pk}", f"cancel:keep:{event.pk}"]

    def it_warns_about_the_whole_series_for_a_recurring_event(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published(recurrence=CommunityEvent.Recurrence.WEEKLY)
        assert "removes the whole repeating series" in _confirm_card(admin, event.pk)["data"]["content"]


def describe_keeping():
    def it_changes_nothing(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published()
        result = _click(admin, "keep", event.pk)
        assert "Kept. Nothing changed." in result["data"]["content"]
        assert CommunityEvent.objects.filter(pk=event.pk).exists()


def describe_confirming():
    @respx.mock
    def it_unwinds_google_and_discord_then_deletes(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published()

        with (
            patch.object(CommunityEvent, "remove_from_google") as google,
            patch.object(CommunityEvent, "remove_from_discord") as discord,
        ):
            result = _click(admin, "confirm", event.pk)

        assert result == {}
        assert google.called and discord.called
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()
        payload = json.loads(followup.calls.last.request.content)
        assert "cancelled and removed" in payload["content"]
        assert payload["components"] == []  # the confirm buttons are stripped

    @respx.mock
    def it_reports_a_failed_unwind_without_a_stacktrace(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published()

        with patch.object(CommunityEvent, "remove_from_google", side_effect=Exception("boom")):
            result = _click(admin, "confirm", event.pk)

        assert result == {}
        assert "was not fully removed" in json.loads(followup.calls.last.request.content)["content"]
        assert CommunityEvent.objects.filter(pk=event.pk).exists()  # nothing was deleted


def describe_click_time_rechecks():
    def it_reports_already_handled_when_the_event_vanished(linked_member):
        member = linked_member()
        assert "already handled" in _click(member, "confirm", 424242)["data"]["content"]

    def it_reports_lost_authority_when_the_state_shifted(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published()
        # Taken back to a draft between the card and the click: no longer cancellable here.
        CommunityEvent.objects.filter(pk=event.pk).update(moderation_state=CommunityEvent.ModerationState.DRAFT)
        content = _click(admin, "confirm", event.pk)["data"]["content"]
        assert "Ask a lead or admin to remove it" in content
        assert CommunityEvent.objects.filter(pk=event.pk).exists()

    def it_reports_setup_incomplete_for_a_userless_member():
        member = MemberFactory()
        assert "isn't fully set up" in _click(member, "confirm", 1)["data"]["content"]


def describe_malformed_clicks():
    @pytest.mark.parametrize(
        "custom_id",
        ["cancel:nope:1", "cancel:confirm:abc", "cancel:pick", "cancel:confirm:1:2", "cancel"],
    )
    def it_error_replies(custom_id, linked_member):
        # A bare "cancel" still routes here by prefix; it must land on the malformed reply, not an
        # IndexError. "cancel:pick" was the retired /cancel picker's select.
        result = _cancel_component({"data": {"custom_id": custom_id}}, linked_member())
        assert "went wrong" in result["data"]["content"]
