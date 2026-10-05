"""BDD specs for the reservation scope helpers behind the Reservations Bookings tab (#627).

``manageable_reservations`` is the queryset form of ``can_manage_equipment`` read through a
reservation's equipment. The parity spec builds reservations on a standalone tool, two guild
owned tools and a room, and asserts, for every viewer, that a reservation is in the queryset
exactly when ``can_manage_equipment`` is true for its equipment. ``honour_preview`` gives the
tab's list scope; the action gate never moves. ``manages_equipment`` decides the staff view.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from classes.factories import UserFactory
from hub.view_as import ROLE_ADMIN, ROLE_GUEST, ROLE_GUILD_OFFICER, ROLE_MEMBER, ViewAs
from membership.models import AdminCapability, Equipment, EquipmentReservation, GuildStaffMembership, Member
from membership.permissions import can_manage_equipment, manageable_reservations, manages_equipment
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

ADMIN_ROLES = {ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER}


def _member() -> Member:
    MembershipPlanFactory()
    return UserFactory().member


def _request(member: Member | None, *, roles: set[str], picked: str | None = None) -> Any:
    request = RequestFactory().get("/")
    request.user = member.user if member is not None else AnonymousUser()
    request.view_as = ViewAs(actual=frozenset(roles), picked=picked)
    return request


def _world() -> dict[str, Any]:
    """A standalone lathe, two guilds' tools and a standalone room, one reservation on each."""
    guild_a = GuildFactory(name="Alpha Guild")
    guild_b = GuildFactory(name="Beta Guild")
    items = {
        "lathe": EquipmentFactory(name="Standalone Lathe"),
        "saw": EquipmentFactory(name="Alpha Saw", guild=guild_a),
        "laser": EquipmentFactory(name="Beta Laser", guild=guild_b),
        "room": EquipmentFactory(name="Big Room", kind=Equipment.Kind.ROOM),
    }
    return {
        "guild_a": guild_a,
        "guild_b": guild_b,
        "items": items,
        "reservations": {key: EquipmentReservationFactory(equipment=item) for key, item in items.items()},
    }


def _viewers(world: dict[str, Any]) -> dict[str, tuple[Any, set[str]]]:
    """Every viewer role, as (request, the reservation keys the action gate should let through)."""
    lead_a = _member()
    world["guild_a"].guild_lead = lead_a
    world["guild_a"].save()
    staff_b = _member()
    GuildStaffMembershipFactory(guild=world["guild_b"], member=staff_b, role=GuildStaffMembership.Role.SECRETARY)
    lathe_staff = _member()
    EquipmentStaffMembershipFactory(equipment=world["items"]["lathe"], member=lathe_staff)
    holder = _member()
    holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
    previewing_holder = _member()
    previewing_holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
    member_roles = {ROLE_MEMBER}
    return {
        "plain member": (_request(_member(), roles=member_roles), set()),
        "guild A lead": (_request(lead_a, roles=member_roles), {"saw"}),
        "guild B staff": (_request(staff_b, roles=member_roles), {"laser"}),
        "lathe staff": (_request(lathe_staff, roles=member_roles), {"lathe"}),
        "EQUIPMENT holder": (_request(holder, roles=member_roles), {"lathe", "saw", "laser", "room"}),
        "officer (no blanket grant)": (_request(_member(), roles={ROLE_GUILD_OFFICER, ROLE_MEMBER}), set()),
        "admin": (_request(_member(), roles=ADMIN_ROLES), {"lathe", "saw", "laser", "room"}),
        "admin previewing as member": (_request(_member(), roles=ADMIN_ROLES, picked=ROLE_MEMBER), set()),
        "EQUIPMENT admin previewing as member": (
            _request(previewing_holder, roles=ADMIN_ROLES, picked=ROLE_MEMBER),
            {"lathe", "saw", "laser", "room"},
        ),
        "lead previewing as guest": (_request(lead_a, roles=ADMIN_ROLES, picked=ROLE_GUEST), set()),
    }


def describe_manageable_reservations():
    def it_matches_can_manage_equipment_for_every_viewer_and_reservation():
        world = _world()
        for label, (request, expected) in _viewers(world).items():
            in_scope = set(
                manageable_reservations(request, EquipmentReservation.objects.all()).values_list("pk", flat=True)
            )
            for key, reservation in world["reservations"].items():
                gate = can_manage_equipment(request, reservation.equipment)
                assert (reservation.pk in in_scope) is gate, f"{label}: {key}"
                assert gate is (key in expected), f"{label}: {key} (the gate itself)"

    def it_never_repeats_a_row_for_a_lead_who_is_also_staff_twice_over():
        world = _world()
        lead = _member()
        world["guild_a"].guild_lead = lead
        world["guild_a"].save()
        GuildStaffMembershipFactory(guild=world["guild_a"], member=lead, role=GuildStaffMembership.Role.CO_LEAD)
        EquipmentStaffMembershipFactory(equipment=world["items"]["saw"], member=lead)
        rows = list(manageable_reservations(_request(lead, roles={ROLE_MEMBER}), EquipmentReservation.objects.all()))
        assert rows == [world["reservations"]["saw"]]

    def it_is_empty_for_an_anonymous_request():
        _world()
        request = _request(None, roles={ROLE_GUEST})
        assert not manageable_reservations(request, EquipmentReservation.objects.all()).exists()


def describe_honour_preview():
    def it_drops_a_previewed_capability_from_the_list_scope_only():
        world = _world()
        admin = _member()
        admin.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(admin, roles=ADMIN_ROLES, picked=ROLE_MEMBER)
        everything = EquipmentReservation.objects.all()
        assert manageable_reservations(request, everything).count() == 4
        assert not manageable_reservations(request, everything, honour_preview=True).exists()
        assert can_manage_equipment(request, world["items"]["lathe"]) is True  # the action gate is unchanged

    def it_keeps_a_lead_s_own_scope_through_honour_preview():
        world = _world()
        lead = _member()
        world["guild_a"].guild_lead = lead
        world["guild_a"].save()
        request = _request(lead, roles={ROLE_MEMBER})
        rows = manageable_reservations(request, EquipmentReservation.objects.all(), honour_preview=True)
        assert list(rows) == [world["reservations"]["saw"]]


def describe_manages_equipment():
    def it_answers_for_every_role():
        world = _world()
        answers = {label: manages_equipment(request) for label, (request, _expected) in _viewers(world).items()}
        assert answers == {
            "plain member": False,
            "guild A lead": True,
            "guild B staff": True,
            "lathe staff": True,
            "EQUIPMENT holder": True,
            "officer (no blanket grant)": False,
            "admin": True,
            "admin previewing as member": False,
            "EQUIPMENT admin previewing as member": True,
            "lead previewing as guest": False,
        }

    def it_drops_a_previewed_capability_for_the_staff_view():
        admin = _member()
        admin.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(admin, roles=ADMIN_ROLES, picked=ROLE_MEMBER)
        assert manages_equipment(request) is True
        assert manages_equipment(request, honour_preview=True) is False

    def it_says_no_for_a_lead_whose_guild_owns_nothing():
        lead = _member()
        GuildFactory(guild_lead=lead)
        assert manages_equipment(_request(lead, roles={ROLE_MEMBER})) is False
