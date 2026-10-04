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
from membership.models import AdminCapability, Equipment, GuildStaffMembership, Member
from membership.permissions import (
    can_create_equipment,
    can_edit_equipment_orienter_hours,
    can_manage_equipment,
    creatable_equipment_kinds,
    manageable_equipment_ids,
)
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


def describe_manageable_equipment_ids():
    """The bulk form of ``can_manage_equipment`` (#502): the guild settings item list asks once."""

    def _world() -> dict[str, object]:
        lead = _member_user()
        guild_a = GuildFactory(guild_lead=lead)
        guild_b = GuildFactory()
        staff = _member_user()
        GuildStaffMembershipFactory(guild=guild_a, member=staff, role=GuildStaffMembership.Role.SECRETARY)
        names = {"a1": guild_a, "a2": guild_a, "b1": guild_b, "solo": None, "solo2": None}
        made = {name: EquipmentFactory(name=f"Parity {name}", guild=guild) for name, guild in names.items()}
        manager = _member_user()
        EquipmentStaffMembershipFactory(equipment=made["solo"], member=manager)
        # A lead who also manages one item of another guild: the guild and resource tiers together.
        EquipmentStaffMembershipFactory(equipment=made["b1"], member=lead)
        capability = _member_user()
        capability.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        return {"lead": lead, "staff": staff, "manager": manager, "capability": capability, "items": made}

    def _items() -> list[Equipment]:
        return list(Equipment.objects.select_related("guild").filter(name__startswith="Parity "))

    def it_agrees_with_can_manage_equipment_for_every_tier():
        world = _world()
        made = world["items"]
        everything = set(made)
        cases = [
            ("admin", _request(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER}), everything),
            (
                "admin previewing as member",
                _request(_member_user().user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER),
                set(),
            ),
            ("capability holder", _request(world["capability"].user, roles={ROLE_MEMBER}), everything),
            (
                "capability holder previewing as guest",
                _request(world["capability"].user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_GUEST),
                everything,
            ),
            ("guild officer", _request(_member_user().user, roles={ROLE_GUILD_OFFICER, ROLE_MEMBER}), set()),
            ("lead", _request(world["lead"].user, roles={ROLE_MEMBER}), {"a1", "a2", "b1"}),
            ("guild staff", _request(world["staff"].user, roles={ROLE_MEMBER}), {"a1", "a2"}),
            ("equipment manager", _request(world["manager"].user, roles={ROLE_MEMBER}), {"solo"}),
            ("plain member", _request(_member_user().user, roles={ROLE_MEMBER}), set()),
            ("anonymous", _request(AnonymousUser()), set()),
        ]
        items = _items()
        for label, request, expected_names in cases:
            expected = {made[name].pk for name in expected_names}
            assert manageable_equipment_ids(request, items) == expected, label
            for item in items:
                assert can_manage_equipment(request, item) is (item.pk in expected), (label, item.name)

    def it_gives_the_members_own_answer_when_nobody_is_previewing():
        # The role side (Member.manageable_equipment_ids) holds the tiers; the request side
        # only adds view_as. With no preview the two must agree for every tier.
        world = _world()
        made = world["items"]
        admin = _member_user()
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        members = {
            "admin": (admin, {ROLE_ADMIN, ROLE_MEMBER}),
            "capability holder": (world["capability"], {ROLE_MEMBER}),
            "lead": (world["lead"], {ROLE_MEMBER}),
            "guild staff": (world["staff"], {ROLE_MEMBER}),
            "equipment manager": (world["manager"], {ROLE_MEMBER}),
            "plain member": (_member_user(), {ROLE_MEMBER}),
        }
        items = _items()
        for label, (member, roles) in members.items():
            own = member.manageable_equipment_ids(items)
            assert own == manageable_equipment_ids(_request(member.user, roles=roles), items), label
            for item in items:
                assert member.can_manage_equipment(item) is (item.pk in own), (label, item.name)
        assert members["lead"][0].manageable_equipment_ids(items) == {made[n].pk for n in ("a1", "a2", "b1")}

    def it_answers_an_empty_list_without_a_query(django_assert_num_queries):
        request = _request(_member_user().user, roles={ROLE_MEMBER})
        with django_assert_num_queries(0):
            assert manageable_equipment_ids(request, []) == set()

    def it_answers_an_empty_list_without_a_query_on_the_member_side(django_assert_num_queries):
        member = _member_user()
        with django_assert_num_queries(0):
            assert member.manageable_equipment_ids([]) == set()

    def describe_query_count():
        def _count(request: object, items: list[Equipment]) -> int:
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            with CaptureQueriesContext(connection) as ctx:
                manageable_equipment_ids(request, items)
            return len(ctx.captured_queries)

        def _guild_items(guild: object, count: int) -> list[Equipment]:
            for index in range(count):
                EquipmentFactory(name=f"Counted {guild.pk} {index}", guild=guild)
            return list(Equipment.objects.select_related("guild").filter(guild=guild))

        def it_asks_the_same_for_one_item_or_six_for_guild_staff():
            staff = _member_user()
            one, six = GuildFactory(), GuildFactory()
            for guild in (one, six):
                GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.TREASURER)
            request = _request(staff.user, roles={ROLE_MEMBER})
            assert _count(request, _guild_items(one, 1)) == _count(request, _guild_items(six, 6))

        def it_asks_the_same_for_one_item_or_six_for_an_officer():
            officer = _member_user()
            request = _request(officer.user, roles={ROLE_GUILD_OFFICER, ROLE_MEMBER})
            assert _count(request, _guild_items(GuildFactory(), 1)) == _count(request, _guild_items(GuildFactory(), 6))


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


def describe_creatable_equipment_kinds():
    """#502: the kinds a request may create — every kind, rooms and spaces, or nothing."""

    def it_gives_every_kind_to_an_effective_admin():
        request = _request(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER})
        assert creatable_equipment_kinds(request) == ["tool", "room", "space"]

    def it_gives_every_kind_to_an_equipment_capability_holder():
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(member.user, roles={ROLE_MEMBER})
        assert creatable_equipment_kinds(request) == ["tool", "room", "space"]

    def it_gives_rooms_and_spaces_to_a_space_manager():
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
        request = _request(member.user, roles={ROLE_MEMBER})
        assert creatable_equipment_kinds(request) == ["room", "space"]
        assert can_create_equipment(request) is True

    def it_prefers_the_equipment_grant_when_both_are_held():
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
        request = _request(member.user, roles={ROLE_MEMBER})
        assert creatable_equipment_kinds(request) == ["tool", "room", "space"]

    def it_gives_nothing_to_a_guild_lead():
        lead = _member_user()
        GuildFactory(guild_lead=lead)
        assert creatable_equipment_kinds(_request(lead.user, roles={ROLE_MEMBER})) == []

    def it_gives_nothing_to_a_plain_member():
        member = _member_user()
        assert creatable_equipment_kinds(_request(member.user, roles={ROLE_MEMBER})) == []
        assert can_create_equipment(_request(member.user, roles={ROLE_MEMBER})) is False

    def it_gives_nothing_to_an_anonymous_request():
        assert creatable_equipment_kinds(_request(AnonymousUser())) == []

    def it_demotes_an_admin_previewing_as_member_who_holds_no_grant():
        member = _member_user()
        request = _request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert creatable_equipment_kinds(request) == []

    def it_keeps_a_space_managers_kinds_under_preview():
        # The capability legs are preview-independent, like every house capability gate.
        member = _member_user()
        member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
        request = _request(member.user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_GUEST)
        assert creatable_equipment_kinds(request) == ["room", "space"]


def describe_can_edit_equipment_orienter_hours():
    """The equipment twin of can_edit_orienter_hours: own hours for any manager, others' for the top tier."""

    def _managed_tool():
        equipment = EquipmentFactory(guild=GuildFactory())
        manager = _member_user()
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        return equipment, manager

    def it_refuses_self_scope_to_someone_who_does_not_run_the_tool():
        """An off-roster save would only make rules that never generate — fail loudly.

        Mirrors the guild gate, which refuses self scope off a guild's leadership for the
        same reason. The capability still grants edit-on-behalf (the orienter != self path).
        """
        equipment, _manager = _managed_tool()
        holder = _member_user()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(holder.user, roles={ROLE_MEMBER})
        assert can_edit_equipment_orienter_hours(request, equipment, holder) is False
        EquipmentStaffMembershipFactory(equipment=equipment, member=holder)
        assert can_edit_equipment_orienter_hours(request, equipment, holder) is True

    def it_refuses_self_scope_to_a_full_admin_who_does_not_run_the_tool():
        equipment, _manager = _managed_tool()
        admin = _member_user()
        request = _request(admin.user, roles={ROLE_ADMIN, ROLE_MEMBER})
        assert can_edit_equipment_orienter_hours(request, equipment, admin) is False
        # They keep every on-behalf power, including the shared rows.
        assert can_edit_equipment_orienter_hours(request, equipment, _manager) is True
        assert can_edit_equipment_orienter_hours(request, equipment, None) is True

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
