"""BDD specs for the three event.*_published events: curated-copy lock-step, channels,
and the recipient audiences (incl. the new cross-guild all_guild_leads resolver)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from core.events.copy import placeholders_for, sample_context_for
from core.events.registry import Channel, ChannelDefault, get_event
from core.events.resolvers import all_guild_leads, resolve
from membership.models import GuildStaffMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

_EVENT_KEYS = ["event.guild_published", "event.community_published", "event.lead_meeting_published"]


def _signed_up_member(username: str) -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    return user.member


def describe_curated_copy():
    @pytest.mark.parametrize("key", _EVENT_KEYS)
    def it_keeps_placeholders_and_sample_context_in_lockstep(key):
        assert set(placeholders_for(key)) == set(sample_context_for(key).keys())


def describe_channels():
    @pytest.mark.parametrize("key", _EVENT_KEYS)
    def it_is_in_app_and_discord_with_email_off(key):
        event = get_event(key)
        assert Channel.IN_APP in event.channel_list
        assert Channel.DISCORD in event.channel_list
        assert event.channel(Channel.EMAIL).default is ChannelDefault.OFF


@pytest.mark.django_db
def describe_all_guild_leads_resolver():
    def it_returns_leads_officers_and_staff_but_not_plain_members():
        MembershipPlanFactory()
        lead = _signed_up_member("lead")
        GuildFactory(guild_lead=lead)
        staff = _signed_up_member("staff")
        GuildStaffMembership.objects.create(
            guild=GuildFactory(), member=staff, role=GuildStaffMembership.Role.TREASURER
        )
        officer = _signed_up_member("officer")
        officer.fog_role = Member.FogRole.GUILD_OFFICER
        officer.save(update_fields=["fog_role"])
        plain = _signed_up_member("plain")

        user_pks = {user.pk for user, _reason in all_guild_leads({})}
        assert lead.user.pk in user_pks
        assert staff.user.pk in user_pks
        assert officer.user.pk in user_pks
        assert plain.user.pk not in user_pks

    def it_excludes_a_lead_with_no_usable_account():
        # A lead whose Member has no linked User (Airtable-only) can't be reached.
        no_account_lead = MemberFactory()
        GuildFactory(guild_lead=no_account_lead)
        assert all_guild_leads({}) == []


@pytest.mark.django_db
def describe_event_audiences():
    def it_resolves_lead_meeting_to_all_guild_leads():
        MembershipPlanFactory()
        lead = _signed_up_member("lead2")
        GuildFactory(guild_lead=lead)
        event = get_event("event.lead_meeting_published")
        recipients = resolve(event.recipient, {})
        assert lead.user.pk in {user.pk for user, _ in recipients}

    def it_resolves_guild_published_to_the_guilds_members():
        MembershipPlanFactory()
        guild = GuildFactory()
        member = _signed_up_member("gm")
        GuildMembershipFactory(guild=guild, member=member)
        event = get_event("event.guild_published")
        recipients = resolve(event.recipient, {"guild": guild})
        assert member.user.pk in {user.pk for user, _ in recipients}

    def it_resolves_community_published_to_all_active_members():
        MembershipPlanFactory()
        member = _signed_up_member("am")
        event = get_event("event.community_published")
        recipients = resolve(event.recipient, {})
        assert member.user.pk in {user.pk for user, _ in recipients}
