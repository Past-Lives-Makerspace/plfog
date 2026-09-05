"""BDD specs for the request-level equipment permission helpers (spec §5, PR 1).

``can_manage_equipment`` / ``can_create_equipment`` are ``view_as``-aware like their
siblings in ``membership/permissions.py`` — an admin previewing as a lower role sees
exactly what that viewer would. The role-based twins live on ``Member`` and are
covered in ``equipment_spec.py``.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from classes.factories import UserFactory
from hub.view_as import ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_GUEST, ROLE_MEMBER, ViewAs
from membership.models import AdminCapability, GuildStaffMembership, Member
from membership.permissions import can_create_equipment, can_edit_equipment_orienter_hours, can_manage_equipment
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _member_user() -> Member:
    MembershipPlanFactory()
    return UserFactory().member


def _request(user: object, *, roles: set[str] | None = None, picked: str | None = None) -> object:
    request = RequestFactory().get("/")
    request.user = user
    if roles is not None:
        request.view_as = ViewAs(actual=frozenset(roles), picked=picked)
    return request


def describe_can_manage_equipment():
    def it_allows_an_effective_admin():
        request = _request(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER})
        assert can_manage_equipment(request, EquipmentFactory()) is True

    def it_denies_an_admin_without_the_capability_previewing_as_member():
        # The admin leg demotes under preview. In practice migration 0161 backfills
        # EQUIPMENT onto every existing admin, so a real previewing admin keeps access
        # via the capability leg (pinned below) — this pins the admin leg in isolation.
        member = _member_user()
        request = _request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert can_manage_equipment(request, EquipmentFactory()) is False

    def it_keeps_manage_access_for_a_capability_holding_admin_previewing_as_member():
        # The capability leg is preview-independent (the house capability-gate semantic):
        # a granted duty follows the person, not the view-as preview.
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert can_manage_equipment(request, EquipmentFactory()) is True

    def it_denies_a_guild_officer_without_any_grant():
        # The site tier is deliberately narrower than is_effective_staff (spec §5).
        member = _member_user()
        request = _request(member.user, roles={ROLE_GUILD_OFFICER, ROLE_MEMBER})
        assert can_manage_equipment(request, EquipmentFactory()) is False

    def it_allows_an_equipment_capability_holder():
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(member.user, roles={ROLE_MEMBER})
        assert can_manage_equipment(request, EquipmentFactory()) is True

    def it_allows_the_owning_guilds_lead():
        lead = _member_user()
        guild = GuildFactory(guild_lead=lead)
        request = _request(lead.user, roles={ROLE_MEMBER})
        assert can_manage_equipment(request, EquipmentFactory(guild=guild)) is True

    def it_allows_the_owning_guilds_staff():
        staff = _member_user()
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.TREASURER)
        request = _request(staff.user, roles={ROLE_MEMBER})
        assert can_manage_equipment(request, EquipmentFactory(guild=guild)) is True

    def it_allows_an_equipment_staff_row_holder():
        manager = _member_user()
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        request = _request(manager.user, roles={ROLE_MEMBER})
        assert can_manage_equipment(request, equipment) is True

    def it_denies_a_plain_member():
        member = _member_user()
        request = _request(member.user, roles={ROLE_MEMBER})
        assert can_manage_equipment(request, EquipmentFactory()) is False

    def it_denies_a_lead_of_another_guild():
        lead = _member_user()
        GuildFactory(guild_lead=lead)
        request = _request(lead.user, roles={ROLE_MEMBER})
        assert can_manage_equipment(request, EquipmentFactory(guild=GuildFactory())) is False

    def it_denies_an_anonymous_request():
        request = _request(AnonymousUser())
        assert can_manage_equipment(request, EquipmentFactory()) is False

    def it_keeps_manage_access_for_a_capability_holder_previewing_as_guest():
        # Preview-independent even at the guest extreme — the capability leg never
        # consults view_as, exactly like hub.view_as._capability_or_admin_required.
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_GUEST)
        assert can_manage_equipment(request, EquipmentFactory()) is True


def describe_can_create_equipment():
    def it_allows_an_effective_admin():
        request = _request(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER})
        assert can_create_equipment(request) is True

    def it_allows_an_equipment_capability_holder():
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(member.user, roles={ROLE_MEMBER})
        assert can_create_equipment(request) is True

    def it_denies_a_guild_lead():
        lead = _member_user()
        GuildFactory(guild_lead=lead)
        request = _request(lead.user, roles={ROLE_MEMBER})
        assert can_create_equipment(request) is False

    def it_denies_a_plain_member_and_an_admin_previewing_as_member():
        # Neither holds the capability, so only the (preview-demoted) admin leg applies.
        member = _member_user()
        assert can_create_equipment(_request(member.user, roles={ROLE_MEMBER})) is False
        assert can_create_equipment(_request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)) is False

    def it_keeps_create_access_for_a_capability_holder_previewing_as_member():
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert can_create_equipment(request) is True

    def it_denies_an_anonymous_request():
        assert can_create_equipment(_request(AnonymousUser())) is False


def describe_can_edit_equipment_orienter_hours():
    """The equipment twin of can_edit_orienter_hours: own hours for any manager, others' for the top tier."""

    def _managed_tool():
        equipment = EquipmentFactory(guild=GuildFactory())
        manager = _member_user()
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        return equipment, manager

    def it_lets_a_plain_manager_edit_their_own_hours_only():
        equipment, manager = _managed_tool()
        other = _member_user()
        EquipmentStaffMembershipFactory(equipment=equipment, member=other)
        request = _request(manager.user, roles={ROLE_MEMBER})
        assert can_edit_equipment_orienter_hours(request, equipment, manager) is True
        assert can_edit_equipment_orienter_hours(request, equipment, other) is False
        assert can_edit_equipment_orienter_hours(request, equipment, None) is False

    def it_denies_a_plain_member_even_for_themselves():
        equipment, _manager = _managed_tool()
        member = _member_user()
        assert can_edit_equipment_orienter_hours(_request(member.user, roles={ROLE_MEMBER}), equipment, member) is False

    def it_lets_an_effective_admin_edit_anyone_and_the_shared_rows():
        equipment, manager = _managed_tool()
        request = _request(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER})
        assert can_edit_equipment_orienter_hours(request, equipment, manager) is True
        assert can_edit_equipment_orienter_hours(request, equipment, None) is True

    def it_lets_an_equipment_capability_holder_edit_anyone():
        equipment, manager = _managed_tool()
        holder = _member_user()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(holder.user, roles={ROLE_MEMBER})
        assert can_edit_equipment_orienter_hours(request, equipment, manager) is True
        assert can_edit_equipment_orienter_hours(request, equipment, None) is True

    def it_lets_the_owning_guilds_lead_edit_anyone_but_not_another_guilds_lead():
        equipment, manager = _managed_tool()
        lead = _member_user()
        equipment.guild.guild_lead = lead
        equipment.guild.save(update_fields=["guild_lead"])
        assert can_edit_equipment_orienter_hours(_request(lead.user, roles={ROLE_MEMBER}), equipment, manager) is True
        assert can_edit_equipment_orienter_hours(_request(lead.user, roles={ROLE_MEMBER}), equipment, None) is True
        stranger_lead = _member_user()
        GuildFactory(guild_lead=stranger_lead)
        assert (
            can_edit_equipment_orienter_hours(_request(stranger_lead.user, roles={ROLE_MEMBER}), equipment, manager)
            is False
        )

    def it_denies_a_non_manager_admin_previewing_as_a_member():
        # Own hours ride can_manage_equipment, whose admin leg demotes under preview.
        equipment, manager = _managed_tool()
        admin = _member_user()
        request = _request(admin.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert can_edit_equipment_orienter_hours(request, equipment, admin) is False
        assert can_edit_equipment_orienter_hours(request, equipment, manager) is False
