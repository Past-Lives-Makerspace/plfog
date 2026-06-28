"""Recipient resolvers — role × scope, with the scoped guild + orientation cases."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.events import resolvers
from core.events.registry import Recipients
from membership.models import GuildStaffMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    OrientationBookingFactory,
)

pytestmark = pytest.mark.django_db


def _user_pks(recipients):
    return {u.pk for u, _reason in recipients}


def _reasons(recipients):
    return {reason for _u, reason in recipients}


def describe_member_to_user_mapping():
    def it_drops_members_without_a_linked_user(linked_member):
        from tests.membership.factories import MemberFactory

        unlinked = MemberFactory(user=None)
        assert resolvers._member_user(unlinked, "x") is None

    def it_drops_linked_members_without_an_email(linked_member):
        member = linked_member(email="")
        assert resolvers._member_user(member, "x") is None

    def it_keeps_linked_members_with_an_email(linked_member):
        member = linked_member(email="ok@example.com")
        recipient = resolvers._member_user(member, "why")
        assert recipient is not None
        user, reason = recipient
        assert user.pk == member.user_id
        assert reason == "why"


def describe_fog_admins():
    def it_returns_admin_role_members(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        linked_member(fog_role=Member.FogRole.MEMBER)  # non-admin, excluded
        recipients = resolvers.fog_admins({})
        assert _user_pks(recipients) == {admin.user_id}

    def it_is_global_and_ignores_guild_context(linked_member):
        admin = linked_member(fog_role=Member.FogRole.ADMIN)
        recipients = resolvers.fog_admins({"guild": GuildFactory()})
        assert admin.user_id in _user_pks(recipients)

    def describe_with_configured_notify_emails():
        def it_unions_in_configured_addresses(settings, linked_member):
            extra = User.objects.create_user(username="cfg", email="cfg@example.com")
            settings.CLASS_ADMIN_NOTIFY_EMAILS = "cfg@example.com"
            recipients = resolvers.fog_admins({})
            assert extra.pk in _user_pks(recipients)

        def it_ignores_blank_chunks(settings, linked_member):
            admin = linked_member(fog_role=Member.FogRole.ADMIN)
            settings.CLASS_ADMIN_NOTIFY_EMAILS = " , ,"
            recipients = resolvers.fog_admins({})
            assert _user_pks(recipients) == {admin.user_id}


def describe_guild_leadership():
    def it_includes_lead_and_all_staff(linked_member):
        lead = linked_member()
        staff = linked_member()
        guild = GuildFactory(guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.SECRETARY)
        recipients = resolvers.guild_leadership({"guild": guild})
        assert _user_pks(recipients) == {lead.user_id, staff.user_id}

    def it_is_scoped_to_the_given_guild(linked_member):
        lead_a = linked_member()
        lead_b = linked_member()
        guild_a = GuildFactory(guild_lead=lead_a)
        GuildFactory(guild_lead=lead_b)  # other guild, must not leak in
        recipients = resolvers.guild_leadership({"guild": guild_a})
        assert _user_pks(recipients) == {lead_a.user_id}

    def it_requires_a_guild_in_context():
        with pytest.raises(KeyError):
            resolvers.guild_leadership({})

    def it_resolves_to_nobody_for_an_explicit_none_guild(db):
        # A lead-less category's review routes to admins by email only — the guild is
        # explicitly None, so there is no per-user (in-app) audience.
        assert resolvers.guild_leadership({"guild": None}) == []


def describe_guild_lead():
    def it_returns_only_the_lead_not_staff(linked_member):
        lead = linked_member()
        staff = linked_member()
        guild = GuildFactory(guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.CO_LEAD)
        recipients = resolvers.guild_lead({"guild": guild})
        assert _user_pks(recipients) == {lead.user_id}

    def it_is_empty_when_the_guild_has_no_lead():
        guild = GuildFactory(guild_lead=None)
        assert resolvers.guild_lead({"guild": guild}) == []


def describe_guild_orienters():
    def it_includes_lead_and_orienter_staff_only(linked_member):
        lead = linked_member()
        orienter = linked_member()
        secretary = linked_member()
        guild = GuildFactory(guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=orienter, role=GuildStaffMembership.Role.ORIENTER)
        GuildStaffMembershipFactory(guild=guild, member=secretary, role=GuildStaffMembership.Role.SECRETARY)
        recipients = resolvers.guild_orienters({"guild": guild})
        assert _user_pks(recipients) == {lead.user_id, orienter.user_id}

    def it_does_not_double_count_a_lead_who_is_also_an_orienter(linked_member):
        lead = linked_member()
        guild = GuildFactory(guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=lead, role=GuildStaffMembership.Role.ORIENTER)
        recipients = resolvers.guild_orienters({"guild": guild})
        assert len(recipients) == 1
        assert _user_pks(recipients) == {lead.user_id}


def describe_orientation_runner():
    def it_credits_the_actual_runner_not_the_lead(linked_member):
        runner = linked_member()
        booking = OrientationBookingFactory(oriented_by=runner)
        recipients = resolvers.orientation_runner({"booking": booking})
        assert _user_pks(recipients) == {runner.user_id}

    def it_is_empty_when_no_runner_recorded():
        booking = OrientationBookingFactory(oriented_by=None)
        assert resolvers.orientation_runner({"booking": booking}) == []


def describe_registrant():
    def it_resolves_an_explicit_member(linked_member):
        member = linked_member()
        recipients = resolvers.registrant({"member": member})
        assert _user_pks(recipients) == {member.user_id}

    def it_resolves_the_member_of_a_booking(linked_member):
        member = linked_member()
        booking = OrientationBookingFactory(member=member)
        recipients = resolvers.registrant({"booking": booking})
        assert _user_pks(recipients) == {member.user_id}

    def it_requires_a_resolvable_member():
        with pytest.raises(KeyError):
            resolvers.registrant({})


def describe_instructor():
    def it_resolves_an_explicit_instructor(linked_member):
        member = linked_member()
        recipients = resolvers.instructor({"instructor": member})
        assert _user_pks(recipients) == {member.user_id}

    def it_is_empty_when_instructor_is_none():
        assert resolvers.instructor({"instructor": None}) == []


def describe_next_waitlisted():
    def it_resolves_the_promoted_member(linked_member):
        member = linked_member()
        recipients = resolvers.next_waitlisted({"member": member})
        assert _user_pks(recipients) == {member.user_id}

    def it_is_empty_when_no_one_waitlisted():
        assert resolvers.next_waitlisted({}) == []


def describe_single_user():
    def it_wraps_an_explicit_user():
        user = User.objects.create_user(username="solo", email="solo@example.com")
        recipients = resolvers.single_user({"user": user})
        assert _user_pks(recipients) == {user.pk}

    def it_drops_a_user_without_email():
        user = User.objects.create_user(username="noemail", email="")
        assert resolvers.single_user({"user": user}) == []


def describe_all_active_members():
    def it_returns_active_linked_members(linked_member):
        active = linked_member(status=Member.Status.ACTIVE)
        linked_member(status=Member.Status.FORMER)
        recipients = resolvers.all_active_members({})
        assert active.user_id in _user_pks(recipients)

    def it_excludes_inactive_members(linked_member):
        inactive = linked_member(status=Member.Status.FORMER)
        recipients = resolvers.all_active_members({})
        assert inactive.user_id not in _user_pks(recipients)

    def it_excludes_members_who_never_logged_in(linked_member):
        # Provisioning mints a login for every member, but we never broadcast to one
        # who has never signed in — logging in is what "activates" them.
        never = linked_member(status=Member.Status.ACTIVE, last_login=None)
        recipients = resolvers.all_active_members({})
        assert never.user_id not in _user_pks(recipients)


def describe_all_voters():
    def it_returns_paying_active_members(linked_member):
        voter = linked_member(member_type=Member.MemberType.STANDARD, status=Member.Status.ACTIVE)
        recipients = resolvers.all_voters({})
        assert voter.user_id in _user_pks(recipients)

    def it_excludes_voters_who_never_logged_in(linked_member):
        never = linked_member(member_type=Member.MemberType.STANDARD, status=Member.Status.ACTIVE, last_login=None)
        recipients = resolvers.all_voters({})
        assert never.user_id not in _user_pks(recipients)


def describe_everyone_with_login():
    def it_returns_any_active_user_who_has_signed_in():
        user = User.objects.create_user(
            username="anyone", email="anyone@example.com", is_active=True, last_login=timezone.now()
        )
        recipients = resolvers.everyone_with_login({})
        assert user.pk in _user_pks(recipients)

    def it_excludes_inactive_users():
        user = User.objects.create_user(username="off", email="off@example.com", is_active=False)
        recipients = resolvers.everyone_with_login({})
        assert user.pk not in _user_pks(recipients)

    def it_excludes_users_who_never_logged_in():
        user = User.objects.create_user(username="never", email="never@example.com", is_active=True)
        recipients = resolvers.everyone_with_login({})
        assert user.pk not in _user_pks(recipients)


def describe_resolve_dispatch():
    def it_dispatches_through_the_registry(linked_member):
        member = linked_member()
        recipients = resolvers.resolve(Recipients.REGISTRANT, {"member": member})
        assert _user_pks(recipients) == {member.user_id}

    def it_exposes_named_resolvers():
        assert resolvers.get_resolver(Recipients.FOG_ADMINS) is resolvers.fog_admins
