"""BDD specs for the orientation scope helpers behind the Bookings tab (#626).

``manageable_orientation_bookings`` is the queryset form of ``hub.views._require_can_manage_booking``:
the parity spec builds bookings on two guilds and two pieces of equipment (one standalone,
one guild owned) and asserts, for every viewer, that a booking is in the queryset exactly
when the action gate lets the viewer act on it. That is the guard against the list's scope
and the actions' scope drifting apart. ``manageable_orientation_records`` applies the same
owner rule to hand recorded orientations, and ``manages_orientations`` decides the staff view.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from classes.factories import UserFactory
from hub.view_as import ROLE_ADMIN, ROLE_GUEST, ROLE_GUILD_OFFICER, ROLE_MEMBER, ViewAs
from hub.views import _require_can_manage_booking
from membership.models import AdminCapability, GuildStaffMembership, Member, OrientationBooking, OrientationRecord
from membership.permissions import (
    manageable_orientation_bookings,
    manageable_orientation_records,
    manages_orientations,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationRecordFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _member() -> Member:
    MembershipPlanFactory()
    return UserFactory().member


def _request(member: Member | None, *, roles: set[str], picked: str | None = None) -> Any:
    request = RequestFactory().get("/")
    request.user = member.user if member is not None else AnonymousUser()
    request.view_as = ViewAs(actual=frozenset(roles), picked=picked)
    return request


def _world() -> dict[str, Any]:
    """Two guilds, a standalone tool and a guild owned tool, one booking on each."""
    guild_a = GuildFactory(name="Alpha Guild")
    guild_b = GuildFactory(name="Beta Guild")
    tool_x = EquipmentFactory(name="Standalone Lathe")
    tool_y = EquipmentFactory(name="Beta Laser", guild=guild_b)

    def _equipment_booking(tool: Any) -> OrientationBooking:
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=tool)
        return OrientationBookingFactory(
            slot=OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        )

    return {
        "guild_a": guild_a,
        "guild_b": guild_b,
        "tool_x": tool_x,
        "tool_y": tool_y,
        "bookings": {
            "a": OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild_a)),
            "b": OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild_b)),
            "x": _equipment_booking(tool_x),
            "y": _equipment_booking(tool_y),
        },
    }


def _viewers(world: dict[str, Any]) -> dict[str, tuple[Any, set[str]]]:
    """Every viewer role, as (request, the booking keys the action gate should let through)."""
    plain = _member()
    lead_a = _member()
    world["guild_a"].guild_lead = lead_a
    world["guild_a"].save()
    staff_a = _member()
    GuildStaffMembershipFactory(guild=world["guild_a"], member=staff_a, role=GuildStaffMembership.Role.SECRETARY)
    lead_b = _member()
    world["guild_b"].guild_lead = lead_b
    world["guild_b"].save()
    staff_b = _member()
    GuildStaffMembershipFactory(guild=world["guild_b"], member=staff_b, role=GuildStaffMembership.Role.ORIENTER)
    tool_staff = _member()
    EquipmentStaffMembershipFactory(equipment=world["tool_x"], member=tool_staff)
    holder = _member()
    holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
    officer = _member()
    admin = _member()
    previewing_admin = _member()
    member_roles = {ROLE_MEMBER}
    return {
        "plain member": (_request(plain, roles=member_roles), set()),
        "guild A lead": (_request(lead_a, roles=member_roles), {"a"}),
        "guild A staff": (_request(staff_a, roles=member_roles), {"a"}),
        "guild B lead (owns tool Y)": (_request(lead_b, roles=member_roles), {"b", "y"}),
        "guild B staff (owns tool Y)": (_request(staff_b, roles=member_roles), {"b", "y"}),
        "tool X staff": (_request(tool_staff, roles=member_roles), {"x"}),
        "EQUIPMENT holder": (_request(holder, roles=member_roles), {"x", "y"}),
        "officer": (_request(officer, roles={ROLE_GUILD_OFFICER, ROLE_MEMBER}), {"a", "b"}),
        "admin": (_request(admin, roles={ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER}), {"a", "b", "x", "y"}),
        "admin previewing as member": (
            _request(previewing_admin, roles={ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER}, picked=ROLE_MEMBER),
            set(),
        ),
        "lead previewing as guest": (_request(lead_a, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_GUEST), set()),
    }


def describe_manageable_orientation_bookings():
    def it_matches_the_action_gate_for_every_viewer_and_booking():
        world = _world()
        bookings = world["bookings"]
        for label, (request, expected) in _viewers(world).items():
            in_scope = set(
                manageable_orientation_bookings(request, OrientationBooking.objects.all()).values_list("pk", flat=True)
            )
            for key, booking in bookings.items():
                gate_allows = _require_can_manage_booking(request, booking) is None
                assert (booking.pk in in_scope) is gate_allows, f"{label}: booking {key}"
                assert gate_allows is (key in expected), f"{label}: booking {key} (the gate itself)"

    def it_narrows_the_queryset_it_is_given():
        world = _world()
        admin = _member()
        request = _request(admin, roles={ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER})
        only_a = OrientationBooking.objects.filter(pk=world["bookings"]["a"].pk)
        assert list(manageable_orientation_bookings(request, only_a)) == [world["bookings"]["a"]]

    def it_never_repeats_a_row_for_a_lead_who_is_also_staff():
        world = _world()
        lead = _member()
        world["guild_a"].guild_lead = lead
        world["guild_a"].save()
        GuildStaffMembershipFactory(guild=world["guild_a"], member=lead, role=GuildStaffMembership.Role.CO_LEAD)
        GuildStaffMembershipFactory(guild=world["guild_a"], member=_member(), role=GuildStaffMembership.Role.TREASURER)
        request = _request(lead, roles={ROLE_MEMBER})
        rows = list(manageable_orientation_bookings(request, OrientationBooking.objects.all()))
        assert rows == [world["bookings"]["a"]]

    def it_is_empty_for_an_anonymous_request():
        _world()
        request = _request(None, roles={ROLE_GUEST})
        assert not manageable_orientation_bookings(request, OrientationBooking.objects.all()).exists()


def describe_manageable_orientation_records():
    def it_scopes_records_by_the_same_owner_rule():
        world = _world()
        lead_b = _member()
        world["guild_b"].guild_lead = lead_b
        world["guild_b"].save()
        on_a = OrientationRecordFactory(orientation_type=OrientationTypeFactory(guild=world["guild_a"]))
        on_b = OrientationRecordFactory(orientation_type=OrientationTypeFactory(guild=world["guild_b"]))
        on_x = OrientationRecordFactory(
            orientation_type=OrientationTypeFactory(equipment_owned=True, equipment=world["tool_x"])
        )
        on_y = OrientationRecordFactory(
            orientation_type=OrientationTypeFactory(equipment_owned=True, equipment=world["tool_y"])
        )
        request = _request(lead_b, roles={ROLE_MEMBER})
        assert set(manageable_orientation_records(request, OrientationRecord.objects.all())) == {on_b, on_y}
        officer = _request(_member(), roles={ROLE_GUILD_OFFICER, ROLE_MEMBER})
        assert set(manageable_orientation_records(officer, OrientationRecord.objects.all())) == {on_a, on_b}
        admin = _request(_member(), roles={ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER})
        assert set(manageable_orientation_records(admin, OrientationRecord.objects.all())) == {on_a, on_b, on_x, on_y}


def describe_manages_orientations():
    def it_answers_for_every_role():
        world = _world()
        viewers = _viewers(world)
        answers = {label: manages_orientations(request) for label, (request, _expected) in viewers.items()}
        assert answers == {
            "plain member": False,
            "guild A lead": True,
            "guild A staff": True,
            "guild B lead (owns tool Y)": True,
            "guild B staff (owns tool Y)": True,
            "tool X staff": True,
            "EQUIPMENT holder": True,
            "officer": True,
            "admin": True,
            "admin previewing as member": False,
            "lead previewing as guest": False,
        }

    def it_counts_a_new_lead_with_nothing_booked():
        lead = _member()
        GuildFactory(guild_lead=lead)
        assert manages_orientations(_request(lead, roles={ROLE_MEMBER})) is True


def describe_honour_preview():
    """The Bookings tab's list reads the effective role; the action gates never move (#626 review)."""

    def _previewing_admin_with_equipment() -> Any:
        admin = _member()
        admin.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        return _request(admin, roles={ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER}, picked=ROLE_MEMBER)

    def it_drops_a_previewed_capability_from_the_list_scope_only():
        world = _world()
        request = _previewing_admin_with_equipment()
        everything = OrientationBooking.objects.all()
        tools = {world["bookings"]["x"].pk, world["bookings"]["y"].pk}
        assert set(manageable_orientation_bookings(request, everything).values_list("pk", flat=True)) == tools
        assert not manageable_orientation_bookings(request, everything, honour_preview=True).exists()
        assert _require_can_manage_booking(request, world["bookings"]["x"]) is None  # the action gate is unchanged

    def it_drops_it_from_the_staff_view_question_only():
        request = _previewing_admin_with_equipment()
        assert manages_orientations(request) is True
        assert manages_orientations(request, honour_preview=True) is False

    def it_drops_it_from_the_recorded_list():
        world = _world()
        OrientationRecordFactory(
            orientation_type=OrientationTypeFactory(equipment_owned=True, equipment=world["tool_x"])
        )
        request = _previewing_admin_with_equipment()
        assert not manageable_orientation_records(request, OrientationRecord.objects.all()).exists()

    def it_keeps_the_capability_when_nobody_previews():
        holder = _member()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(holder, roles={ROLE_MEMBER})
        assert manages_orientations(request, honour_preview=True) is True
