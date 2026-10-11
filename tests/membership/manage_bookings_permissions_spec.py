"""BDD specs for the Manage Bookings permission helpers (#768).

``member_runs_anything`` and ``runs_bookings`` are the one gate behind the sidebar entry and
the home; ``manageable_guilds`` is the queryset behind ``guilds_for_new_orientation``; and
``manageable_equipment`` is the queryset form of ``can_manage_equipment``, pinned to it item by
item the way ``manageable_reservations_spec`` pins its twin.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import RequestFactory

from hub.view_as import ROLE_MEMBER, ViewAs, compute_actual_roles
from membership.models import AdminCapability, Equipment, GuildStaffMembership, Member
from membership.permissions import (
    can_manage_equipment,
    guilds_for_new_orientation,
    manageable_equipment,
    manageable_guilds,
    manages_all_equipment,
    member_runs_anything,
    runs_bookings,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def _member(fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    member: Member = UserFactory().member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return member


def _request(rf: RequestFactory, member: Member | None, picked: str | None = None) -> HttpRequest:
    """A request shaped the way ``ViewAsMiddleware`` shapes one; ``picked`` is an admin's preview role."""
    request = rf.get("/")
    request.user = member.user if member is not None else AnonymousUser()
    request.view_as = ViewAs(compute_actual_roles(request.user), picked)  # type: ignore[attr-defined]
    return request


def describe_member_runs_anything():
    def it_is_true_for_a_guild_lead():
        member = _member()
        GuildFactory(guild_lead=member)
        assert member_runs_anything(member) is True

    def it_is_true_for_a_guild_staffer():
        member = _member()
        GuildStaffMembershipFactory(member=member, role=GuildStaffMembership.Role.ORIENTER)
        assert member_runs_anything(member) is True

    def it_is_true_for_an_equipment_manager():
        member = _member()
        EquipmentStaffMembershipFactory(member=member)
        assert member_runs_anything(member) is True

    def it_is_false_for_a_member_on_no_leadership():
        member = _member()
        GuildFactory(guild_lead=_member())
        assert member_runs_anything(member) is False

    def it_costs_one_query_whatever_the_answer(django_assert_num_queries):
        lead = _member()
        GuildFactory(guild_lead=lead)
        EquipmentStaffMembershipFactory(member=lead)
        nobody = _member()
        with django_assert_num_queries(1):
            assert member_runs_anything(lead) is True
        with django_assert_num_queries(1):
            assert member_runs_anything(nobody) is False


def describe_runs_bookings():
    def it_is_true_for_an_admin_without_a_query(rf, django_assert_num_queries):
        request = _request(rf, _member(Member.FogRole.ADMIN))
        with django_assert_num_queries(0):
            assert runs_bookings(request) is True

    def it_is_true_for_a_guild_officer(rf):
        assert runs_bookings(_request(rf, _member(Member.FogRole.GUILD_OFFICER))) is True

    def it_is_true_for_a_member_who_leads_a_guild(rf):
        member = _member()
        GuildFactory(guild_lead=member)
        assert runs_bookings(_request(rf, member)) is True

    def it_is_false_for_a_member_who_runs_nothing(rf):
        assert runs_bookings(_request(rf, _member())) is False

    def it_is_false_for_an_anonymous_visitor(rf):
        assert runs_bookings(_request(rf, None)) is False

    def it_follows_an_admins_preview_as_a_member(rf):
        # Ruling 9: a preview shows the member's sidebar; an admin on no leadership loses the entry.
        admin = _member(Member.FogRole.ADMIN)
        assert runs_bookings(_request(rf, admin, ROLE_MEMBER)) is False
        GuildFactory(guild_lead=admin)
        assert runs_bookings(_request(rf, admin, ROLE_MEMBER)) is True


def describe_manageable_guilds():
    def it_gives_an_admin_every_active_guild_and_no_hidden_one(rf):
        active = GuildFactory(name="Active Guild")
        GuildFactory(name="Hidden Guild", is_active=False)
        request = _request(rf, _member(Member.FogRole.ADMIN))
        assert list(manageable_guilds(request)) == [active]

    def it_gives_a_lead_their_guilds_hidden_ones_included(rf):
        member = _member()
        led = GuildFactory(name="Led Guild", guild_lead=member, is_active=False)
        staffed = GuildFactory(name="Staffed Guild")
        GuildStaffMembershipFactory(guild=staffed, member=member)
        GuildFactory(name="Somebody Else's Guild")
        request = _request(rf, member)
        assert list(manageable_guilds(request)) == [led, staffed]
        # The list form the "+ Add an Orientation" button reads is the same answer.
        assert guilds_for_new_orientation(request) == [led, staffed]

    def it_gives_an_anonymous_visitor_nothing(rf):
        GuildFactory()
        assert list(manageable_guilds(_request(rf, None))) == []


def describe_manageable_equipment():
    @pytest.fixture
    def items() -> dict[str, Equipment]:
        guild = GuildFactory(name="Owner Guild")
        return {
            "guild_owned": EquipmentFactory(name="Guild Lathe", guild=guild),
            "standalone": EquipmentFactory(name="Lobby Printer"),
            "retired": EquipmentFactory(name="Old Saw", is_active=False),
        }

    def _parity(request: HttpRequest, items: dict[str, Equipment], *, honour_preview: bool = False) -> None:
        """The queryset answers exactly what the one item gate answers, item by item."""
        listed = set(
            manageable_equipment(request, Equipment.objects.all(), honour_preview=honour_preview).values_list(
                "pk", flat=True
            )
        )
        for item in items.values():
            assert (item.pk in listed) is can_manage_equipment(request, item), item.name

    def it_gives_an_admin_every_item(rf, items):
        request = _request(rf, _member(Member.FogRole.ADMIN))
        assert set(manageable_equipment(request, Equipment.objects.all())) == set(items.values())
        _parity(request, items)

    def it_gives_an_equipment_administrator_every_item(rf, items):
        member = _member()
        member.sync_admin_capabilities([AdminCapability.Capability.EQUIPMENT])
        request = _request(rf, member)
        assert set(manageable_equipment(request, Equipment.objects.all())) == set(items.values())
        _parity(request, items)

    def it_drops_the_capability_while_previewing_when_asked_to_honour_the_preview(rf, items):
        admin = _member(Member.FogRole.ADMIN)
        admin.sync_admin_capabilities([AdminCapability.Capability.EQUIPMENT])
        EquipmentStaffMembershipFactory(equipment=items["standalone"], member=admin)
        request = _request(rf, admin, ROLE_MEMBER)
        # The action scope keeps the capability: a granted duty follows the person.
        assert set(manageable_equipment(request, Equipment.objects.all())) == set(items.values())
        # The list scope shows what the previewed member would manage: their own staff row only.
        assert list(manageable_equipment(request, Equipment.objects.all(), honour_preview=True)) == [
            items["standalone"]
        ]

    def it_gives_the_owning_guilds_lead_and_staff_that_guilds_items(rf, items):
        lead = _member()
        guild = items["guild_owned"].guild
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        staffer = _member()
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        for member in (lead, staffer):
            request = _request(rf, member)
            assert list(manageable_equipment(request, Equipment.objects.all())) == [items["guild_owned"]]
            _parity(request, items)

    def it_gives_an_equipment_manager_only_their_item(rf, items):
        member = _member()
        EquipmentStaffMembershipFactory(equipment=items["retired"], member=member)
        request = _request(rf, member)
        assert list(manageable_equipment(request, Equipment.objects.all())) == [items["retired"]]
        _parity(request, items)

    def it_gives_a_plain_member_and_an_anonymous_visitor_nothing(rf, items):
        for request in (_request(rf, _member()), _request(rf, None)):
            assert list(manageable_equipment(request, Equipment.objects.all())) == []
            _parity(request, items)

    def it_names_the_site_tier_as_managing_all_of_it(rf, items):
        # The question a list asks before it stops asking "which ones": admin or EQUIPMENT holder.
        assert manages_all_equipment(_request(rf, _member(Member.FogRole.ADMIN))) is True
        holder = _member()
        holder.sync_admin_capabilities([AdminCapability.Capability.EQUIPMENT])
        assert manages_all_equipment(_request(rf, holder)) is True
        previewing = _member(Member.FogRole.ADMIN)
        previewing.sync_admin_capabilities([AdminCapability.Capability.EQUIPMENT])
        assert manages_all_equipment(_request(rf, previewing, ROLE_MEMBER)) is True
        assert manages_all_equipment(_request(rf, previewing, ROLE_MEMBER), honour_preview=True) is False
        lead = _member()
        items["guild_owned"].guild.guild_lead = lead
        items["guild_owned"].guild.save(update_fields=["guild_lead"])
        assert manages_all_equipment(_request(rf, lead)) is False
        assert manages_all_equipment(_request(rf, _member(Member.FogRole.GUILD_OFFICER))) is False
        assert manages_all_equipment(_request(rf, None)) is False

    def it_never_repeats_an_item_a_member_reaches_two_ways(rf, items):
        member = _member()
        guild = items["guild_owned"].guild
        guild.guild_lead = member
        guild.save(update_fields=["guild_lead"])
        EquipmentStaffMembershipFactory(equipment=items["guild_owned"], member=member)
        assert list(manageable_equipment(_request(rf, member), Equipment.objects.all())) == [items["guild_owned"]]
