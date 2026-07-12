"""BDD specs for the event.reminder / event.happening_now spine wiring: the switching
event_audience resolver (per-scope + the per-scope activation-gate parity), the two new
events' channels, their curated copy (absolute event-page links, .txt/.html parity), and
the publish_due_events cron promotion firing exactly once."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.events.copy import default_copy_for, placeholders_for, sample_context_for
from core.events.registry import Channel, ChannelDefault, get_event
from core.events.rendering import render_html, render_text
from core.events.resolvers import event_audience, resolve
from core.models import EventDelivery
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory, GuildMembershipFactory

pytestmark = pytest.mark.django_db

EventType = CommunityEvent.EventType


def _pks(recipients):
    return {u.pk for u, _reason in recipients}


def describe_event_audience_resolver():
    def it_resolves_a_guild_event_to_guild_members_only(linked_member):
        guild = GuildFactory()
        inside = linked_member()
        outside = linked_member()
        GuildMembershipFactory(guild=guild, member=inside)
        recipients = event_audience({"guild": guild, "event_type": EventType.GUILD_MEETING})
        assert inside.user_id in _pks(recipients)
        assert outside.user_id not in _pks(recipients)

    def it_resolves_a_lead_meeting_to_all_guild_leads(linked_member):
        lead = linked_member()
        GuildFactory(guild_lead=lead)
        recipients = event_audience({"guild": None, "event_type": EventType.LEAD_MEETING})
        assert lead.user_id in _pks(recipients)

    def it_resolves_a_community_event_to_all_active_members(linked_member):
        member = linked_member()
        recipients = event_audience({"guild": None, "event_type": EventType.COMMUNITY})
        assert member.user_id in _pks(recipients)

    def it_requires_a_guild_key_in_context():
        with pytest.raises(KeyError):
            event_audience({"event_type": EventType.COMMUNITY})


def describe_activation_gate_parity():
    def it_keeps_a_never_logged_in_guild_member(linked_member):
        # Parity with event.guild_published: guild_members is NOT activation-gated.
        guild = GuildFactory()
        never = linked_member(last_login=None)
        GuildMembershipFactory(guild=guild, member=never)
        recipients = event_audience({"guild": guild, "event_type": EventType.GUILD_MEETING})
        assert never.user_id in _pks(recipients)

    def it_drops_a_never_logged_in_community_member(linked_member):
        never = linked_member(last_login=None)
        recipients = event_audience({"guild": None, "event_type": EventType.COMMUNITY})
        assert never.user_id not in _pks(recipients)

    def it_drops_a_never_logged_in_lead_for_a_lead_meeting(linked_member):
        never = linked_member(last_login=None)
        GuildFactory(guild_lead=never)
        recipients = event_audience({"guild": None, "event_type": EventType.LEAD_MEETING})
        assert never.user_id not in _pks(recipients)


def describe_new_events_route_through_event_audience():
    @pytest.mark.parametrize("key", ["event.reminder", "event.happening_now"])
    def it_uses_the_event_audience_resolver(key):
        assert get_event(key).recipient.value == "event_audience"

    def it_resolves_a_guild_reminder_to_the_guilds_members(linked_member):
        guild = GuildFactory()
        member = linked_member()
        GuildMembershipFactory(guild=guild, member=member)
        event = get_event("event.reminder")
        recipients = resolve(event.recipient, {"guild": guild, "event_type": EventType.GUILD_MEETING})
        assert member.user_id in _pks(recipients)


def describe_new_event_channels():
    def it_makes_reminder_in_app_only_with_email_and_discord_off():
        event = get_event("event.reminder")
        assert event.has_channel(Channel.IN_APP)
        assert event.channel(Channel.EMAIL).default is ChannelDefault.OFF
        assert event.channel(Channel.DISCORD).default is ChannelDefault.OFF

    def it_broadcasts_happening_now_to_discord_with_email_off():
        event = get_event("event.happening_now")
        assert event.has_channel(Channel.IN_APP)
        assert event.channel(Channel.EMAIL).default is ChannelDefault.OFF
        assert event.channel(Channel.DISCORD).default is ChannelDefault.ON


def describe_new_event_copy():
    @pytest.mark.parametrize("key", ["event.reminder", "event.happening_now"])
    def it_keeps_placeholders_and_sample_context_in_lockstep(key):
        assert set(placeholders_for(key)) == set(sample_context_for(key).keys())

    @pytest.mark.parametrize("key", ["event.reminder", "event.happening_now"])
    def it_links_the_absolute_event_page_in_both_bodies(key):
        ctx = sample_context_for(key)
        copy = default_copy_for(key, Channel.EMAIL)
        html = str(render_html(copy.body_html, ctx))
        text = render_text(copy.body_text, ctx)
        assert "/events/5/" in html
        assert "/events/5/" in text
        assert "See the event details" in html
        assert "See the event details" in text

    def it_falls_back_to_email_copy_for_the_discord_post():
        # happening_now posts to Discord (default ON) but has no explicit DISCORD copy —
        # copy_for() must fall back to the EMAIL body so the channel post is authored.
        email = default_copy_for("event.happening_now", Channel.EMAIL)
        discord = default_copy_for("event.happening_now", Channel.DISCORD)
        assert discord.body_text == email.body_text


def describe_publish_due_events_command():
    def it_publishes_a_due_scheduled_event_exactly_once(linked_member):
        guild = GuildFactory()
        self_member = linked_member()
        GuildMembershipFactory(guild=guild, member=self_member)
        event = CommunityEventFactory(
            guild=guild,
            moderation_state=CommunityEvent.ModerationState.SCHEDULED,
            publish_at=timezone.now() - timedelta(minutes=1),
        )
        # Nothing announced while parked.
        assert EventDelivery.objects.filter(event_key="event.guild_published").count() == 0

        call_command("publish_due_events")
        call_command("publish_due_events")  # a second pass must not double-announce

        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert EventDelivery.objects.filter(event_key="event.guild_published", channel="in_app").count() == 1

    def it_leaves_a_future_scheduled_event_untouched(linked_member):
        event = CommunityEventFactory(
            moderation_state=CommunityEvent.ModerationState.SCHEDULED,
            publish_at=timezone.now() + timedelta(days=2),
        )
        call_command("publish_due_events")
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.SCHEDULED

    def it_no_ops_when_nothing_is_due():
        call_command("publish_due_events")  # empty batch — the no-due branch, no error

    def it_keeps_going_when_one_publish_raises():
        event = CommunityEventFactory(
            moderation_state=CommunityEvent.ModerationState.SCHEDULED,
            publish_at=timezone.now() - timedelta(minutes=1),
        )
        with patch.object(CommunityEvent, "publish_scheduled", side_effect=RuntimeError("boom")):
            call_command("publish_due_events")  # the per-row try/except swallows it
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.SCHEDULED


def describe_send_event_reminders_command():
    def it_reports_a_fired_count():
        with patch("core.management.commands.send_event_reminders.run_sources", return_value=2) as mock_run:
            call_command("send_event_reminders")
        mock_run.assert_called_once()

    def it_reports_when_nothing_is_due():
        call_command("send_event_reminders")  # no events → the else branch, no error
