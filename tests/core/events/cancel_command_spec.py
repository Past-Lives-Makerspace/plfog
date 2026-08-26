"""Specs for the ``/cancel`` slash command (membership.discord_commands).

Covers the cancellable-set membership per authority tier, the empty state, the select →
confirm-card → withdraw / delete / keep flow, and every already-handled / authority-lost
reply. The delete branch's Google + Discord unwind runs behind a type-6 ack (Discord
REST mocked with ``respx``).
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from django.utils import timezone

from membership.discord_commands import _cancel, _cancel_component, _cancellable_events, CANCEL
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


def _future(**kwargs) -> dict:
    starts = timezone.now() + timedelta(days=3)
    defaults = {"starts_at": starts, "ends_at": starts + timedelta(hours=1)}
    defaults.update(kwargs)
    return defaults


def _proposal(member, **overrides) -> CommunityEvent:
    fields = {"moderation_state": CommunityEvent.ModerationState.PENDING, "submitted_by": member.user}
    fields.update(overrides)
    return CommunityEventFactory(**_future(**fields))


def _published(**overrides) -> CommunityEvent:
    return CommunityEventFactory(**_future(moderation_state=CommunityEvent.ModerationState.PUBLISHED, **overrides))


def _pick(member, pk) -> dict:
    interaction = {"data": {"custom_id": "cancel:pick", "values": [str(pk)]}}
    return _cancel_component(interaction, member)


def _click(member, action: str, pk) -> dict:
    interaction = {"id": "intA", "token": "tokB", "data": {"custom_id": f"cancel:{action}:{pk}"}}
    return _cancel_component(interaction, member)


def describe_command_definition():
    def it_is_link_gated_ephemeral_and_not_deferred():
        assert CANCEL.name == "cancel"
        assert (CANCEL.requires_link, CANCEL.ephemeral, CANCEL.defer) == (True, True, False)
        assert CANCEL.scope == "guild"


def describe_the_cancellable_set():
    def it_includes_the_members_own_pending_and_changes_requested_proposals(linked_member):
        member = linked_member()
        pending = _proposal(member)
        changes = _proposal(member, moderation_state=CommunityEvent.ModerationState.CHANGES_REQUESTED)
        _proposal(linked_member())  # someone else's — excluded
        assert set(_cancellable_events(member)) == {pending, changes}

    def it_gives_an_admin_every_upcoming_published_event(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        site_wide = _published(guild=None, event_type=CommunityEvent.EventType.COMMUNITY)
        guild_event = _published(guild=GuildFactory(name="Clay"))
        assert set(_cancellable_events(admin)) == {site_wide, guild_event}

    def it_gives_a_guild_lead_only_their_guilds_events(linked_member):
        lead = linked_member()
        mine = GuildFactory(name="Mine")
        mine.guild_lead = lead
        mine.save(update_fields=["guild_lead"])
        ours = _published(guild=mine)
        _published(guild=GuildFactory(name="Theirs"))
        _published(guild=None, event_type=CommunityEvent.EventType.COMMUNITY)  # site-wide → admin only
        assert _cancellable_events(lead) == [ours]

    def it_excludes_past_one_off_events_but_keeps_recurring_series(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        past = timezone.now() - timedelta(days=10)
        CommunityEventFactory(
            moderation_state=CommunityEvent.ModerationState.PUBLISHED,
            starts_at=past,
            ends_at=past + timedelta(hours=1),
        )
        series = CommunityEventFactory(
            moderation_state=CommunityEvent.ModerationState.PUBLISHED,
            starts_at=past,
            ends_at=past + timedelta(hours=1),
            recurrence=CommunityEvent.Recurrence.WEEKLY,
        )
        assert _cancellable_events(admin) == [series]

    def it_sorts_soonest_first(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        later = _published(guild=None, event_type=CommunityEvent.EventType.COMMUNITY)
        sooner_start = timezone.now() + timedelta(days=1)
        sooner = _published(
            guild=None,
            event_type=CommunityEvent.EventType.COMMUNITY,
            starts_at=sooner_start,
            ends_at=sooner_start + timedelta(hours=1),
        )
        assert _cancellable_events(admin) == [sooner, later]


def describe_the_picker():
    def it_reports_setup_incomplete_for_a_userless_member():
        member = MemberFactory()
        assert "isn't fully set up" in _cancel({"data": {"options": []}}, member)["data"]["content"]

    def it_shows_the_empty_state_when_nothing_is_cancellable(linked_member):
        member = linked_member()
        content = _cancel({"data": {"options": []}}, member)["data"]["content"]
        assert "no upcoming events you can cancel" in content
        assert "ask a lead or admin" in content

    def it_lists_the_events_in_a_select_menu(linked_member):
        member = linked_member()
        event = _proposal(member, title="My Proposal")
        result = _cancel({"data": {"options": []}}, member)
        select = result["data"]["components"][0]["components"][0]
        assert select["custom_id"] == "cancel:pick"
        assert [o["value"] for o in select["options"]] == [str(event.pk)]
        assert "Only your next" not in result["data"]["content"]

    def it_caps_the_picker_and_says_so(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        for offset in range(26):
            start = timezone.now() + timedelta(days=1, hours=offset)
            CommunityEventFactory(
                moderation_state=CommunityEvent.ModerationState.PUBLISHED,
                guild=None,
                event_type=CommunityEvent.EventType.COMMUNITY,
                starts_at=start,
                ends_at=start + timedelta(hours=1),
            )
        result = _cancel({"data": {"options": []}}, admin)
        select = result["data"]["components"][0]["components"][0]
        assert len(select["options"]) == 25
        assert "Only your next 25 are listed" in result["data"]["content"]


def describe_the_confirm_card():
    def it_shows_the_withdraw_copy_for_an_own_proposal(linked_member):
        member = linked_member()
        event = _proposal(member, title="My Proposal")
        result = _pick(member, event.pk)
        assert result["type"] == 7  # in-place UPDATE_MESSAGE
        content = result["data"]["content"]
        assert "Withdraw your proposal" in content
        assert "comes off the review queue" in content
        buttons = result["data"]["components"][0]["components"]
        assert [b["custom_id"] for b in buttons] == [f"cancel:confirm:{event.pk}", f"cancel:keep:{event.pk}"]

    def it_shows_the_delete_copy_for_a_published_event(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published(guild=None, event_type=CommunityEvent.EventType.COMMUNITY, title="Big Night")
        content = _pick(admin, event.pk)["data"]["content"]
        assert "Cancel **Big Night**" in content
        assert "Google Calendar, and Discord" in content
        assert "repeating series" not in content

    def it_warns_about_the_whole_series_for_a_recurring_event(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published(
            guild=None, event_type=CommunityEvent.EventType.COMMUNITY, recurrence=CommunityEvent.Recurrence.WEEKLY
        )
        assert "removes the whole repeating series" in _pick(admin, event.pk)["data"]["content"]

    def it_reports_a_vanished_event_as_already_handled(linked_member):
        member = linked_member()
        assert "already handled" in _pick(member, 424242)["data"]["content"]

    def it_reports_lost_authority_with_a_pointer_to_staff(linked_member):
        member = linked_member()
        event = _published(guild=GuildFactory(name="Not Yours"))
        content = _pick(member, event.pk)["data"]["content"]
        assert "Ask a lead or admin to remove it" in content


def describe_keeping():
    def it_changes_nothing(linked_member):
        member = linked_member()
        event = _proposal(member)
        result = _click(member, "keep", event.pk)
        assert "Kept. Nothing changed." in result["data"]["content"]
        assert CommunityEvent.objects.filter(pk=event.pk).exists()


def describe_confirming_a_withdraw():
    def it_deletes_the_proposal_without_touching_discord(linked_member):
        member = linked_member()
        event = _proposal(member)
        with respx.mock:  # no routes mocked — any HTTP call would fail the spec
            result = _click(member, "confirm", event.pk)
        assert "Proposal withdrawn." in result["data"]["content"]
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()

    def it_reports_already_handled_when_the_transition_is_no_longer_valid(linked_member):
        member = linked_member()
        event = _proposal(member)
        # A pushed proposal (has a google id) refuses to withdraw — surfaced as the friendly copy.
        CommunityEvent.objects.filter(pk=event.pk).update(google_event_id="g123")
        result = _click(member, "confirm", event.pk)
        assert "already handled" in result["data"]["content"]
        assert CommunityEvent.objects.filter(pk=event.pk).exists()


def describe_confirming_a_delete():
    @respx.mock
    def it_unwinds_google_and_discord_then_deletes(settings, linked_member):
        _discord_settings(settings)
        followup = _mock_discord()
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published(guild=None, event_type=CommunityEvent.EventType.COMMUNITY)

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
        event = _published(guild=None, event_type=CommunityEvent.EventType.COMMUNITY)

        with patch.object(CommunityEvent, "remove_from_google", side_effect=Exception("boom")):
            result = _click(admin, "confirm", event.pk)

        assert result == {}
        assert "was not fully posted" in json.loads(followup.calls.last.request.content)["content"]
        assert CommunityEvent.objects.filter(pk=event.pk).exists()  # nothing was deleted


def describe_click_time_rechecks():
    def it_reports_already_handled_when_the_event_vanished(linked_member):
        member = linked_member()
        assert "already handled" in _click(member, "confirm", 424242)["data"]["content"]

    def it_reports_lost_authority_when_the_state_shifted(linked_member):
        member = linked_member()
        event = _proposal(member)
        # A reviewer published it between pick and confirm — the member has no delete authority.
        CommunityEvent.objects.filter(pk=event.pk).update(
            moderation_state=CommunityEvent.ModerationState.PUBLISHED, submitted_by=None
        )
        content = _click(member, "confirm", event.pk)["data"]["content"]
        assert "Ask a lead or admin to remove it" in content
        assert CommunityEvent.objects.filter(pk=event.pk).exists()

    def it_reports_setup_incomplete_for_a_userless_member():
        member = MemberFactory()
        assert "isn't fully set up" in _click(member, "confirm", 1)["data"]["content"]


def describe_malformed_clicks():
    def it_errors_on_an_unknown_action(linked_member):
        member = linked_member()
        result = _cancel_component({"data": {"custom_id": "cancel:nope:1:2"}}, member)
        assert "went wrong" in result["data"]["content"]

    def it_errors_on_a_non_numeric_target(linked_member):
        member = linked_member()
        result = _cancel_component({"data": {"custom_id": "cancel:confirm:abc"}}, member)
        assert "went wrong" in result["data"]["content"]

    def it_errors_on_a_pick_with_no_values(linked_member):
        member = linked_member()
        result = _cancel_component({"data": {"custom_id": "cancel:pick", "values": []}}, member)
        assert "went wrong" in result["data"]["content"]
