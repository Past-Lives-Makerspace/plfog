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
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
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

        def it_drops_a_configured_address_with_no_account(settings, linked_member):
            # Since #524 the staff emails go through this resolver: an address with no
            # account has no switches to obey, so it gets nothing.
            admin = linked_member(fog_role=Member.FogRole.ADMIN)
            settings.CLASS_ADMIN_NOTIFY_EMAILS = "nobody@example.com"
            assert _user_pks(resolvers.fog_admins({})) == {admin.user_id}

        def it_counts_an_admin_who_is_also_configured_once(settings, linked_member):
            admin = linked_member(fog_role=Member.FogRole.ADMIN, email="both@example.com")
            settings.CLASS_ADMIN_NOTIFY_EMAILS = "both@example.com, BOTH@example.com"
            recipients = resolvers.fog_admins({})
            assert [user.pk for user, _reason in recipients] == [admin.user_id]


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


def describe_guild_members():
    def it_includes_everyone_on_the_guild_roster(linked_member):
        joined = linked_member()
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=joined)
        recipients = resolvers.guild_members({"guild": guild})
        assert _user_pks(recipients) == {joined.user_id}

    def it_excludes_a_member_who_has_not_joined(linked_member):
        joined = linked_member()
        outsider = linked_member()
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=joined)
        recipients = resolvers.guild_members({"guild": guild})
        assert _user_pks(recipients) == {joined.user_id}
        assert outsider.user_id not in _user_pks(recipients)

    def it_includes_a_directory_hidden_member(linked_member):
        # Directory privacy governs the public roster, not whether you hear from your
        # own guild — a hidden member is still part of the guild's audience.
        hidden = linked_member()
        hidden.show_in_directory = False
        hidden.save(update_fields=["show_in_directory"])
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=hidden)
        recipients = resolvers.guild_members({"guild": guild})
        assert _user_pks(recipients) == {hidden.user_id}

    def it_resolves_to_nobody_for_an_empty_guild():
        assert resolvers.guild_members({"guild": GuildFactory()}) == []


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

    def describe_when_include_never_logged_in_is_set():
        def it_adds_active_members_who_never_logged_in(linked_member):
            logged_in = linked_member(status=Member.Status.ACTIVE)
            never = linked_member(status=Member.Status.ACTIVE, last_login=None)
            recipients = resolvers.all_active_members({"include_never_logged_in": True})
            assert {logged_in.user_id, never.user_id} <= _user_pks(recipients)

        def it_still_excludes_inactive_members_and_blank_emails(linked_member):
            former = linked_member(status=Member.Status.FORMER, last_login=None)
            blank = linked_member(status=Member.Status.ACTIVE, last_login=None)
            User.objects.filter(pk=blank.user_id).update(email="")
            recipients = resolvers.all_active_members({"include_never_logged_in": True})
            assert former.user_id not in _user_pks(recipients)
            assert blank.user_id not in _user_pks(recipients)


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

    def _signed_in_with_status(email: str, status: str) -> User:
        from membership.services.provisioning import provision_user_for_member

        member = MemberFactory(_pre_signup_email=email, status=status)
        provision_user_for_member(member)
        member.status = status
        member.save(update_fields=["status"])
        User.objects.filter(pk=member.user_id).update(last_login=timezone.now())
        return member.user

    def it_leaves_out_a_guest_account_from_a_class_booking():
        guest = _signed_in_with_status("guest@example.com", Member.Status.GUEST)
        assert guest.pk not in _user_pks(resolvers.everyone_with_login({}))
        assert guest.pk not in _user_pks(resolvers.release_audience({}))

    def it_still_includes_a_signed_in_former_member():
        former = _signed_in_with_status("former@example.com", Member.Status.FORMER)
        assert former.pk in _user_pks(resolvers.everyone_with_login({}))


def describe_resolve_dispatch():
    def it_dispatches_through_the_registry(linked_member):
        member = linked_member()
        recipients = resolvers.resolve(Recipients.REGISTRANT, {"member": member})
        assert _user_pks(recipients) == {member.user_id}

    def it_exposes_named_resolvers():
        assert resolvers.get_resolver(Recipients.FOG_ADMINS) is resolvers.fog_admins


def describe_guild_orienters_or_equipment_managers():
    """The composed orientation_requested audience — equipment leg vs guild leg, never a union."""

    def _linked(username):
        from tests.membership.factories import MembershipPlanFactory

        MembershipPlanFactory()
        return User.objects.create_user(username=username, email=f"{username}@example.com").member

    def it_routes_an_equipment_context_to_its_managers_and_guild_leadership_only():
        from membership.models import AdminCapability, EquipmentStaffMembership
        from tests.membership.factories import EquipmentFactory

        lead = _linked("goem_lead")
        guild = GuildFactory(guild_lead=lead)
        equipment = EquipmentFactory(guild=guild)
        row_manager = _linked("goem_row")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=row_manager)
        holder = _linked("goem_cap")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        # The row manager also holds the capability: resolves once, as a manager.
        row_manager.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        recipients = resolvers.resolve(
            Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS, {"equipment": equipment, "slot": None}
        )
        # #746: a holder who does not run this equipment hears nothing about it.
        assert _user_pks(recipients) == {lead.user.pk, row_manager.user.pk}

    def _staffed_guild(prefix):
        lead = _linked(f"{prefix}_lead")
        guild = GuildFactory(guild_lead=lead)
        orienter = _linked(f"{prefix}_orienter")
        GuildStaffMembershipFactory(guild=guild, member=orienter, role=GuildStaffMembership.Role.ORIENTER)
        treasurer = _linked(f"{prefix}_treasurer")
        GuildStaffMembershipFactory(guild=guild, member=treasurer, role=GuildStaffMembership.Role.TREASURER)
        return guild, lead, orienter, treasurer

    def it_routes_a_guild_shared_slot_to_the_whole_leadership():
        # #524: one audience for the email and the bell. The email always went to the whole
        # team; now the bell does too, so a treasurer who can confirm hears about it.
        from tests.membership.factories import OrientationSlotFactory

        guild, lead, orienter, treasurer = _staffed_guild("goem_s")
        shared = OrientationSlotFactory(guild=guild)
        recipients = resolvers.resolve(
            Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS, {"guild": guild, "slot": shared}
        )
        assert _user_pks(recipients) == {lead.user.pk, orienter.user.pk, treasurer.user.pk}

    def it_routes_a_guild_context_with_no_slot_to_the_whole_leadership():
        guild, lead, orienter, treasurer = _staffed_guild("goem_n")
        recipients = resolvers.resolve(Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS, {"guild": guild})
        assert _user_pks(recipients) == {lead.user.pk, orienter.user.pk, treasurer.user.pk}

    def it_narrows_a_guild_personal_slot_to_the_orienter_and_the_lead():
        from tests.membership.factories import OrientationSlotFactory

        guild, lead, orienter, _treasurer = _staffed_guild("goem_p")
        personal = OrientationSlotFactory(guild=guild, orienter=orienter)
        recipients = resolvers.resolve(
            Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS, {"guild": guild, "slot": personal}
        )
        assert _user_pks(recipients) == {lead.user.pk, orienter.user.pk}

    def it_leaves_guild_orienters_itself_lead_plus_orienters():
        # Other callers use guild_orienters; the orientation audience change is not theirs.
        guild, lead, orienter, _treasurer = _staffed_guild("goem_o")
        assert _user_pks(resolvers.guild_orienters({"guild": guild})) == {lead.user.pk, orienter.user.pk}

    def it_fails_loudly_with_neither_key():
        with pytest.raises(KeyError):
            resolvers.resolve(Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS, {"slot": None})


def describe_equipment_managers_personal_slot_narrowing():
    """A personal equipment slot in context narrows the audience like guild_orienters does."""

    def _linked(username):
        from tests.membership.factories import MembershipPlanFactory

        MembershipPlanFactory()
        return User.objects.create_user(username=username, email=f"{username}@example.com").member

    def it_narrows_to_the_manager_and_the_owning_guild_lead_without_the_capability_holders():
        from membership.models import AdminCapability, EquipmentStaffMembership
        from tests.membership.factories import EquipmentFactory, OrientationSlotFactory, OrientationTypeFactory

        lead = _linked("emn_lead")
        guild = GuildFactory(guild_lead=lead)
        staffer = _linked("emn_staffer")
        GuildStaffMembershipFactory(guild=guild, member=staffer, role=GuildStaffMembership.Role.TREASURER)
        equipment = EquipmentFactory(guild=guild)
        dana = _linked("emn_dana")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=dana)
        other = _linked("emn_other")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=other)
        holder = _linked("emn_holder")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Basics")
        personal = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type, orienter=dana)
        shared = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)

        narrowed = resolvers.equipment_managers({"equipment": equipment, "slot": personal})
        # The guild lead only, not the guild's whole leadership: the staffer is left out.
        assert _user_pks(narrowed) == {dana.user_id, lead.user_id}
        assert staffer.user_id not in _user_pks(narrowed)
        everyone = resolvers.equipment_managers({"equipment": equipment, "slot": shared})
        assert _user_pks(everyone) == {dana.user_id, other.user_id, lead.user_id, staffer.user_id}
        assert holder.user_id not in _user_pks(narrowed) | _user_pks(everyone)

    def it_narrows_to_the_manager_alone_on_a_standalone_tool():
        from membership.models import EquipmentStaffMembership
        from tests.membership.factories import EquipmentFactory, OrientationSlotFactory, OrientationTypeFactory

        equipment = EquipmentFactory(guild=None)
        dana = _linked("emn_solo")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=dana)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Basics")
        personal = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type, orienter=dana)
        assert _user_pks(resolvers.equipment_managers({"equipment": equipment, "slot": personal})) == {dana.user_id}


def describe_equipment_managers_audience():
    """#746: the people who run the equipment; Equipment Administrators only when nobody does."""

    def _linked(username):
        from tests.membership.factories import MembershipPlanFactory

        MembershipPlanFactory()
        return User.objects.create_user(username=username, email=f"{username}@example.com").member

    def _holder(username):
        from membership.models import AdminCapability

        holder = _linked(username)
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        return holder

    def it_reaches_only_the_manager_of_a_guildless_tool():
        from membership.models import EquipmentStaffMembership
        from tests.membership.factories import EquipmentFactory

        equipment = EquipmentFactory(guild=None)
        sami = _linked("ema_sami")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=sami)
        amber = _holder("ema_amber")
        recipients = resolvers.resolve(Recipients.EQUIPMENT_MANAGERS, {"equipment": equipment})
        assert _user_pks(recipients) == {sami.user_id}
        assert amber.user_id not in _user_pks(recipients)

    def it_reaches_the_managers_and_the_whole_guild_leadership_of_a_guild_tool():
        from membership.models import EquipmentStaffMembership
        from tests.membership.factories import EquipmentFactory

        lead = _linked("ema_g_lead")
        guild = GuildFactory(guild_lead=lead)
        staffer = _linked("ema_g_staff")
        GuildStaffMembershipFactory(guild=guild, member=staffer, role=GuildStaffMembership.Role.TREASURER)
        equipment = EquipmentFactory(guild=guild)
        manager = _linked("ema_g_mgr")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=manager)
        # The lead also manages the tool: resolves once, tagged as a manager.
        EquipmentStaffMembership.objects.create(equipment=equipment, member=lead)
        _holder("ema_g_holder")
        recipients = resolvers.resolve(Recipients.EQUIPMENT_MANAGERS, {"equipment": equipment})
        assert _user_pks(recipients) == {lead.user_id, staffer.user_id, manager.user_id}
        assert len(recipients) == 3
        reasons = {user.pk: reason for user, reason in recipients}
        assert reasons[lead.user_id] == "equipment_staff"
        assert reasons[staffer.user_id] == "guild_leadership"

    def it_falls_back_to_the_equipment_administrators_for_a_tool_nobody_runs():
        from tests.membership.factories import EquipmentFactory

        equipment = EquipmentFactory(guild=None)
        holder = _holder("ema_fb_holder")
        recipients = resolvers.resolve(Recipients.EQUIPMENT_MANAGERS, {"equipment": equipment})
        assert _user_pks(recipients) == {holder.user_id}
        assert {reason for _user, reason in recipients} == {"capability:equipment"}

    def it_falls_back_when_the_owning_guild_has_no_leadership_either():
        from tests.membership.factories import EquipmentFactory

        equipment = EquipmentFactory(guild=GuildFactory(guild_lead=None))
        holder = _holder("ema_fb_empty_guild")
        recipients = resolvers.resolve(Recipients.EQUIPMENT_MANAGERS, {"equipment": equipment})
        assert _user_pks(recipients) == {holder.user_id}

    def it_never_falls_back_once_anyone_runs_the_tool():
        from membership.models import EquipmentStaffMembership
        from tests.membership.factories import EquipmentFactory

        equipment = EquipmentFactory(guild=None)
        manager = _linked("ema_nf_mgr")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=manager)
        _holder("ema_nf_holder")
        assert _user_pks(resolvers.equipment_managers({"equipment": equipment, "slot": None})) == {manager.user_id}
