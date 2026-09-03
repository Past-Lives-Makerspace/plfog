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
from membership.permissions import can_create_equipment, can_manage_equipment
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

    def it_denies_an_admin_previewing_as_member():
        member = _member_user()
        request = _request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert can_manage_equipment(request, EquipmentFactory()) is False

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

    def it_denies_a_capability_holder_previewing_as_guest():
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_GUEST)
        assert can_manage_equipment(request, EquipmentFactory()) is False


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
        member = _member_user()
        assert can_create_equipment(_request(member.user, roles={ROLE_MEMBER})) is False
        assert can_create_equipment(_request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)) is False

    def it_denies_an_anonymous_request():
        assert can_create_equipment(_request(AnonymousUser())) is False
