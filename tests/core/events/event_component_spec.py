"""Specs for the ``event`` component handler (membership.discord_commands).

Covers the RSVP toggle's type-7 rebuild, the ended / recurring / gone states, the ⚙ Manage
card per authority tier (including the creator-without-authority honest card and the correct
edit-URL branch), the cancelcard jump into the existing confirm card, the full
manage → cancel → confirm chain (Discord REST mocked with ``respx``), and the malformed /
unlinked funnels.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.events.discord_commands import dispatch_component
from core.events.discord_interactions import error_reply
from membership.discord_commands import _event_component
from membership.models import CommunityEvent, EventRSVP, Member
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

_CALLBACK_URL = "https://discord.com/api/v10/interactions/intA/tokB/callback"
_FOLLOWUP_URL = "https://discord.com/api/v10/webhooks/appX/tokB/messages/@original"


def _linked_member(**kwargs) -> Member:
    member = MemberFactory(**kwargs)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"evt_u_{member.pk}", email=f"evt_u_{member.pk}@example.com")
    member.user = user
    member.save(update_fields=["user"])
    return member


def _published(**overrides) -> CommunityEvent:
    starts = timezone.now() + timedelta(days=3)
    fields = {"community": True, "starts_at": starts, "ends_at": starts + timedelta(hours=2)}
    fields.update(overrides)
    return CommunityEventFactory(**fields)


def _click(member, action: str, pk) -> dict:
    interaction = {"id": "intA", "token": "tokB", "data": {"custom_id": f"event:{action}:{pk}"}}
    return _event_component(interaction, member)


def _discord_settings(settings) -> None:
    settings.DISCORD_BOT_TOKEN = "bot"
    settings.DISCORD_CLIENT_ID = "appX"
    settings.MEMBER_BASE_URL = "https://members.example"
    settings.DISCORD_NOTIFY_WEBHOOK_URL = ""


def describe_rsvp_click():
    def it_adds_the_rsvp_and_rebuilds_the_message_in_place():
        event = _published()
        member = MemberFactory()
        result = _click(member, "rsvp", event.pk)
        assert result["type"] == 7  # UPDATE_MESSAGE, edits the announcement in place
        assert "embeds" in result["data"]
        assert "components" not in result["data"]  # omitted → Discord keeps the existing buttons
        assert EventRSVP.objects.filter(event=event, member=member).exists()
        assert member.display_name in result["data"]["embeds"][0]["fields"][-1]["value"]

    def it_toggles_off_on_the_second_click():
        event = _published()
        member = MemberFactory()
        _click(member, "rsvp", event.pk)
        _click(member, "rsvp", event.pk)
        assert not EventRSVP.objects.filter(event=event, member=member).exists()

    def it_refuses_a_finished_one_off_without_touching_the_message():
        past = timezone.now() - timedelta(days=2)
        event = _published(starts_at=past, ends_at=past + timedelta(hours=1))
        member = MemberFactory()
        result = _click(member, "rsvp", event.pk)
        assert result["type"] == 4  # a fresh ephemeral, not an in-place edit
        assert "already ended" in result["data"]["content"]
        assert not EventRSVP.objects.filter(event=event).exists()

    def it_keeps_accepting_rsvps_for_a_recurring_series_with_a_past_anchor():
        past = timezone.now() - timedelta(days=30)
        event = _published(
            recurrence=CommunityEvent.Recurrence.WEEKLY, starts_at=past, ends_at=past + timedelta(hours=1)
        )
        member = MemberFactory()
        assert _click(member, "rsvp", event.pk)["type"] == 7
        assert EventRSVP.objects.filter(event=event, member=member).exists()

    def it_shows_the_gone_copy_for_a_missing_event():
        result = _click(MemberFactory(), "rsvp", 999999)
        assert "no longer on the calendar" in result["data"]["content"]

    def it_shows_the_gone_copy_for_an_unpublished_event():
        pending = CommunityEventFactory(pending=True)
        assert "no longer on the calendar" in _click(MemberFactory(), "rsvp", pending.pk)["data"]["content"]

    def it_reflects_the_true_db_state_after_a_second_members_click():
        event = _published()
        first, second = MemberFactory(), MemberFactory()
        _click(first, "rsvp", event.pk)
        result = _click(second, "rsvp", event.pk)
        value = result["data"]["embeds"][0]["fields"][-1]["value"]
        assert first.display_name in value and second.display_name in value


def describe_manage_click():
    def it_shows_the_edit_and_cancel_card_to_an_admin(settings):
        _discord_settings(settings)
        admin = _linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published(title="Big Night")
        result = _click(admin, "manage", event.pk)
        assert result["type"] == 4 and result["data"]["flags"] == 64  # ephemeral card, never in-place
        assert "**Managing Big Night**" in result["data"]["content"]
        buttons = result["data"]["components"][0]["components"]
        assert buttons[0]["label"] == "Edit on the hub"
        assert buttons[1]["custom_id"] == f"event:cancelcard:{event.pk}"

    def it_points_a_site_wide_edit_at_the_admin_editor(settings):
        from django.urls import reverse

        _discord_settings(settings)
        admin = _linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published()  # site-wide (community)
        url = _click(admin, "manage", event.pk)["data"]["components"][0]["components"][0]["url"]
        assert url == f"https://members.example{reverse('hub_event_edit', args=[event.pk])}"

    def it_points_a_guild_edit_at_the_guild_event_editor(settings):
        from django.urls import reverse

        _discord_settings(settings)
        lead = _linked_member()
        guild = GuildFactory(name="Clay")
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        event = CommunityEventFactory(guild=guild)
        url = _click(lead, "manage", event.pk)["data"]["components"][0]["components"][0]["url"]
        assert url == f"https://members.example{reverse('hub_guild_event_edit', args=[guild.pk, event.pk])}"

    def it_shows_the_edit_card_to_guild_staff(settings):
        _discord_settings(settings)
        staffer = _linked_member()
        guild = GuildFactory(name="Metal")
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        event = CommunityEventFactory(guild=guild)
        buttons = _click(staffer, "manage", event.pk)["data"]["components"][0]["components"]
        assert any(b["label"] == "Edit on the hub" for b in buttons)

    def it_shows_an_honest_card_to_a_creator_without_edit_authority(settings):
        _discord_settings(settings)
        creator = _linked_member()
        event = _published(created_by=creator.user)
        result = _click(creator, "manage", event.pk)
        content = result["data"]["content"]
        assert "handled by a guild lead or admin" in content
        buttons = result["data"]["components"][0]["components"]
        assert [b["label"] for b in buttons] == ["Open the event page"]  # no Edit, no Cancel

    def it_refuses_an_unauthorized_clicker_with_a_link_to_the_page(settings):
        _discord_settings(settings)
        stranger = _linked_member()
        event = CommunityEventFactory(guild=GuildFactory(name="Not Theirs"))
        result = _click(stranger, "manage", event.pk)
        assert "Only the organizer or a guild lead" in result["data"]["content"]
        assert event.public_url in result["data"]["content"]

    def it_shows_the_gone_copy_for_a_missing_event():
        assert "no longer on the calendar" in _click(_linked_member(), "manage", 999999)["data"]["content"]


def describe_cancelcard_click():
    def it_edits_the_card_into_the_existing_confirm_card(settings):
        _discord_settings(settings)
        admin = _linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published(title="Big Night")
        result = _click(admin, "cancelcard", event.pk)
        assert result["type"] == 7  # edits the ephemeral manage card in place
        buttons = result["data"]["components"][0]["components"]
        assert [b["custom_id"] for b in buttons] == [f"cancel:confirm:{event.pk}", f"cancel:keep:{event.pk}"]

    def it_reports_lost_authority_when_the_state_shifted():
        member = _linked_member()  # not a lead/admin/creator
        event = _published()
        result = _click(member, "cancelcard", event.pk)
        assert result["type"] == 7
        assert "no longer cancel that event" in result["data"]["content"]


def describe_the_manage_to_cancel_chain():
    @respx.mock
    def it_deletes_the_event_and_strips_the_announcement_buttons(settings):
        from membership.discord_commands import _cancel_component

        _discord_settings(settings)
        respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        followup = respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))
        admin = _linked_member(fog_role=Member.FogRole.ADMIN)
        event = _published()

        confirm_card = _click(admin, "cancelcard", event.pk)
        assert confirm_card["data"]["components"][0]["components"][0]["custom_id"] == f"cancel:confirm:{event.pk}"

        confirm = {"id": "intA", "token": "tokB", "data": {"custom_id": f"cancel:confirm:{event.pk}"}}
        with (
            patch.object(CommunityEvent, "remove_from_google") as google,
            patch.object(CommunityEvent, "remove_from_discord") as discord,
            patch.object(CommunityEvent, "strip_discord_announcement_buttons") as strip,
        ):
            result = _cancel_component(confirm, admin)

        assert result == {}
        assert google.called and discord.called and strip.called
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()
        assert "cancelled and removed" in json.loads(followup.calls.last.request.content)["content"]


def describe_malformed_and_unlinked():
    @pytest.mark.parametrize("custom_id", ["event:rsvp:x", "event:bogus:1", "event:rsvp", "event:manage:1:2"])
    def it_error_replies_on_a_malformed_custom_id(custom_id):
        interaction = {"data": {"custom_id": custom_id}}
        assert _event_component(interaction, _linked_member()) == error_reply()

    def it_prompts_a_clicker_who_unlinked_since_invoking(rf):
        import membership.discord_commands  # noqa: F401  # registers the "event" prefix

        interaction = {"type": 3, "data": {"custom_id": "event:rsvp:1"}, "member": {"user": {"id": "000"}}}
        result = dispatch_component(interaction, rf.post("/"))
        assert result["type"] == 4  # a fresh ephemeral connect prompt
        assert result["data"]["components"][0]["components"][0]["url"].endswith("/discord/link/")
