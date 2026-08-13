"""Capability-scoped recipient resolvers: holders first (default), other admins optional.

The load-bearing case is a holder who is ALSO an admin — the holder concatenation must
come first so ``_dedupe`` (keep-first-reason) keeps them a DEFAULT recipient, never
demoting them to the optional opt-in tier.
"""

from __future__ import annotations

import pytest

from core.events import resolvers
from core.events.resolvers import OPTIONAL_RECIPIENT_REASON
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _grant(member, capability):
    member.admin_capabilities.create(capability=capability)


def _reasons(recipients):
    return {user.pk: reason for user, reason in recipients}


def describe_capability_recipients():
    def it_tags_a_holder_with_the_capability_reason(linked_member):
        holder = linked_member()
        _grant(holder, AdminCapability.Capability.CLASS_APPROVER)
        assert _reasons(resolvers.class_approvers({}))[holder.user.pk] == "capability:class_approver"

    def it_tags_a_non_holder_admin_as_optional(linked_member):
        holder = linked_member()
        _grant(holder, AdminCapability.Capability.CLASS_APPROVER)
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        assert _reasons(resolvers.class_approvers({}))[admin.user.pk] == OPTIONAL_RECIPIENT_REASON

    def it_keeps_a_holder_who_is_also_an_admin_as_a_default_not_optional(linked_member):
        both = linked_member(fog_role=Member.FogRole.ADMIN)
        _grant(both, AdminCapability.Capability.CLASS_APPROVER)
        recipients = resolvers.class_approvers({})
        # The default (capability) reason wins — ordering keeps the holder ahead of the
        # optional admin sweep — and they appear exactly once.
        assert _reasons(recipients)[both.user.pk] == "capability:class_approver"
        assert [user.pk for user, _ in recipients].count(both.user.pk) == 1

    def it_returns_nobody_when_there_are_no_holders_or_admins(db):
        assert resolvers.billing_approvers({}) == []

    def it_drops_a_holder_without_a_usable_user(db):
        from tests.membership.factories import MemberFactory

        member = MemberFactory()  # no linked user
        _grant(member, AdminCapability.Capability.SPACE_APPROVER)
        assert resolvers.space_approvers({}) == []


def describe_guild_leadership_or_class_approvers():
    def it_routes_a_guild_led_class_to_guild_leadership_only(linked_member):
        lead = linked_member()
        guild = GuildFactory(guild_lead=lead)
        # A capability holder exists but must NOT be pulled in for a guild-led class.
        approver = linked_member()
        _grant(approver, AdminCapability.Capability.CLASS_APPROVER)
        pks = {user.pk for user, _ in resolvers.guild_leadership_or_class_approvers({"guild": guild})}
        assert pks == {lead.user.pk}

    def it_routes_a_lead_less_category_to_class_approvers(linked_member):
        approver = linked_member()
        _grant(approver, AdminCapability.Capability.CLASS_APPROVER)
        pks = {user.pk for user, _ in resolvers.guild_leadership_or_class_approvers({"guild": None})}
        assert pks == {approver.user.pk}

    def it_fails_loudly_without_a_guild_key(db):
        with pytest.raises(KeyError):
            resolvers.guild_leadership_or_class_approvers({})


def describe_guild_leadership_or_events_approvers():
    def it_routes_a_guild_proposal_to_guild_leadership_only(linked_member):
        lead = linked_member()
        guild = GuildFactory(guild_lead=lead)
        approver = linked_member()
        _grant(approver, AdminCapability.Capability.EVENTS_APPROVER)
        pks = {user.pk for user, _ in resolvers.guild_leadership_or_events_approvers({"guild": guild})}
        assert pks == {lead.user.pk}

    def it_routes_a_site_wide_proposal_to_events_approvers(linked_member):
        approver = linked_member()
        _grant(approver, AdminCapability.Capability.EVENTS_APPROVER)
        pks = {user.pk for user, _ in resolvers.guild_leadership_or_events_approvers({"guild": None})}
        assert pks == {approver.user.pk}
