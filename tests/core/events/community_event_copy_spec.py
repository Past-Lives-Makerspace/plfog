"""BDD specs for the three event.*_published events: curated-copy lock-step, channels,
and the recipient audiences (incl. the new cross-guild all_guild_leads resolver)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.events.copy import default_copy_for, placeholders_for, sample_context_for
from core.events.registry import Channel, ChannelDefault, get_event
from core.events.rendering import render_html, render_text
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
    # last_login set → an "activated" member the broadcast resolvers will address.
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )
    return user.member


def describe_curated_copy():
    @pytest.mark.parametrize("key", _EVENT_KEYS)
    def it_keeps_placeholders_and_sample_context_in_lockstep(key):
        assert set(placeholders_for(key)) == set(sample_context_for(key).keys())


def describe_channels():
    @pytest.mark.parametrize("key", _EVENT_KEYS)
    def it_is_in_app_email_and_discord_on_by_default(key):
        # Owner call (copy-review 2026-08-18): email is ON by default for these three so a
        # member who joined a guild hears about its events unless they opt out.
        event = get_event(key)
        assert Channel.IN_APP in event.channel_list
        assert Channel.DISCORD in event.channel_list
        assert event.channel(Channel.EMAIL).default is ChannelDefault.ON


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

    def it_excludes_a_lead_who_never_logged_in():
        # Activation gate: a lead with an account who has never signed in is skipped.
        MembershipPlanFactory()
        user = User.objects.create_user(username="dormant", email="dormant@example.com", password="x")
        GuildFactory(guild_lead=user.member)
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


def describe_event_url_copy_points_at_the_event_page():
    """After repointing absolute_url from the calendar to each event's own page, the
    four event_url blocks must link to the event page (not '/calendar/') and say so."""

    @pytest.mark.parametrize(
        "key",
        ["event.guild_published", "event.community_published", "event.lead_meeting_published", "event.approved"],
    )
    def it_renders_the_event_page_url_and_updated_link_text(key):
        ctx = sample_context_for(key)
        copy = default_copy_for(key, Channel.EMAIL)
        html = str(render_html(copy.body_html, ctx))
        text = render_text(copy.body_text, ctx)
        assert "/events/5/" in html
        assert "/events/5/" in text
        assert "/calendar/" not in html
        assert "/calendar/" not in text
        assert "See the event details" in html
        assert "See the event details" in text

    @pytest.mark.parametrize("key", ["event.changes_requested", "event.declined"])
    def it_never_surfaces_an_event_url_on_a_non_publish_decision(key):
        # changes_requested renders edit_url only, declined renders propose_url only —
        # neither has a live public page, so neither may carry a dead event-page link.
        assert "event_url" not in sample_context_for(key)
        copy = default_copy_for(key, Channel.EMAIL)
        assert "{{ event_url }}" not in copy.body_html
        assert "{{ event_url }}" not in copy.body_text
