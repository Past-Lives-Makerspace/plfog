"""BDD specs for the four member-event workflow events: curated-copy lock-step and the
recipient audiences (the new guild-leadership-OR-admins union resolver + SINGLE_USER)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.events.copy import placeholders_for, sample_context_for
from core.events.registry import get_event
from core.events.resolvers import guild_leadership_or_admins, resolve
from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

_EVENT_KEYS = ["event.submitted", "event.approved", "event.changes_requested", "event.declined"]


def _user(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def describe_curated_copy():
    @pytest.mark.parametrize("key", _EVENT_KEYS)
    def it_keeps_placeholders_and_sample_context_in_lockstep(key):
        assert set(placeholders_for(key)) == set(sample_context_for(key).keys())


@pytest.mark.django_db
def describe_guild_leadership_or_admins():
    def it_returns_the_guilds_leadership_plus_admins_for_a_guild_event():
        lead = _user("lead")
        guild = GuildFactory(guild_lead=lead.member)
        admin = _user("admin")
        admin.member.fog_role = Member.FogRole.ADMIN
        admin.member.save(update_fields=["fog_role"])
        plain = _user("plain")

        user_pks = {u.pk for u, _ in guild_leadership_or_admins({"guild": guild})}
        assert lead.pk in user_pks
        assert admin.pk in user_pks
        assert plain.pk not in user_pks

    def it_returns_admins_only_for_a_site_wide_proposal():
        lead = _user("lead2")
        GuildFactory(guild_lead=lead.member)
        admin = _user("admin2")
        admin.member.fog_role = Member.FogRole.ADMIN
        admin.member.save(update_fields=["fog_role"])

        user_pks = {u.pk for u, _ in guild_leadership_or_admins({"guild": None})}
        assert admin.pk in user_pks
        assert lead.pk not in user_pks

    def it_dedupes_a_lead_who_is_also_an_admin():
        person = _user("both")
        person.member.fog_role = Member.FogRole.ADMIN
        person.member.save(update_fields=["fog_role"])
        guild = GuildFactory(guild_lead=person.member)
        recipients = guild_leadership_or_admins({"guild": guild})
        assert [u.pk for u, _ in recipients].count(person.pk) == 1


@pytest.mark.django_db
def describe_decision_audiences():
    @pytest.mark.parametrize("key", ["event.approved", "event.changes_requested", "event.declined"])
    def it_resolves_to_the_submitter(key):
        submitter = _user("submitter")
        event = get_event(key)
        recipients = resolve(event.recipient, {"user": submitter})
        assert {u.pk for u, _ in recipients} == {submitter.pk}

    def it_routes_submitted_to_the_events_approver_composition_resolver():
        assert get_event("event.submitted").recipient.value == "guild_leadership_or_events_approvers"
