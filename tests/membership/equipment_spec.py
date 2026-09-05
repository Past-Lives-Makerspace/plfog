"""BDD specs for the Equipment directory models (equipment-reservations spec PR 1).

Covers ``Equipment`` (slug, querysets, access_state, booking_blockers,
manager_members, FK delete protection), ``EquipmentStaffMembership``, the
EQUIPMENT capability backfill migration, and the ``Member`` permission twins.
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from membership.models import (
    AdminCapability,
    Equipment,
    EquipmentStaffMembership,
    GuildStaffMembership,
    Member,
    OrientationBooking,
    OrientationSlot,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
    SpaceFactory,
)

pytestmark = pytest.mark.django_db


def _completed_orientation(member: Member, orientation_type: object) -> None:
    slot = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
    OrientationBookingFactory(member=member, slot=slot, is_completed=True)


def describe_Equipment():
    def it_stringifies_with_its_kind():
        equipment = EquipmentFactory(name="CNC Router", kind=Equipment.Kind.TOOL)
        assert str(equipment) == "CNC Router (Tool)"

    def describe_slug():
        def it_generates_from_the_name():
            equipment = EquipmentFactory(name="CNC Router")
            assert equipment.slug == "cnc-router"

        def it_stays_stable_across_renames():
            equipment = EquipmentFactory(name="CNC Router")
            equipment.name = "Big CNC Router"
            equipment.save()
            equipment.refresh_from_db()
            assert equipment.slug == "cnc-router"

        def it_suffixes_on_collision():
            EquipmentFactory(name="Laser Cutter")
            second = EquipmentFactory(name="Laser Cutter")
            assert second.slug == "laser-cutter-2"

        def it_falls_back_when_the_name_has_no_sluggable_characters():
            equipment = EquipmentFactory(name="???")
            assert equipment.slug == "equipment"

    def describe_delete_protection():
        def it_protects_a_guild_that_owns_equipment():
            guild = GuildFactory()
            EquipmentFactory(guild=guild)
            with pytest.raises(ProtectedError):
                guild.delete()

        def it_protects_an_orientation_type_that_gates_equipment():
            orientation_type = OrientationTypeFactory()
            EquipmentFactory(required_orientation=orientation_type)
            with pytest.raises(ProtectedError):
                orientation_type.delete()

        def it_nulls_the_space_link_when_the_space_goes():
            space = SpaceFactory()
            equipment = EquipmentFactory(space=space)
            space.delete()
            equipment.refresh_from_db()
            assert equipment.space is None

    def describe_querysets():
        def it_filters_active_for_guild_and_standalone():
            guild = GuildFactory()
            owned = EquipmentFactory(guild=guild)
            standalone = EquipmentFactory(guild=None)
            retired = EquipmentFactory(guild=guild, is_active=False)
            assert set(Equipment.objects.active()) == {owned, standalone}
            assert set(Equipment.objects.for_guild(guild)) == {owned, retired}
            assert set(Equipment.objects.standalone()) == {standalone}

    def describe_manager_members():
        def it_unions_staff_rows_guild_leadership_and_capability_holders_deduped():
            lead = MemberFactory()
            guild_staff = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            GuildStaffMembershipFactory(guild=guild, member=guild_staff, role=GuildStaffMembership.Role.SECRETARY)
            equipment = EquipmentFactory(guild=guild)
            equipment_manager = MemberFactory()
            EquipmentStaffMembershipFactory(equipment=equipment, member=equipment_manager)
            capability_holder = MemberFactory()
            capability_holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
            # The equipment manager ALSO holds the capability — must appear once.
            equipment_manager.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)

            managers = equipment.manager_members()

            assert {m.pk for m in managers} == {lead.pk, guild_staff.pk, equipment_manager.pk, capability_holder.pk}
            assert len(managers) == 4

        def it_skips_the_guild_tier_for_standalone_equipment():
            equipment = EquipmentFactory(guild=None)
            manager = MemberFactory()
            EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
            assert {m.pk for m in equipment.manager_members()} == {manager.pk}

        def it_counts_a_lead_who_also_holds_a_staff_row_once():
            lead = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            equipment = EquipmentFactory(guild=guild)
            EquipmentStaffMembershipFactory(equipment=equipment, member=lead)
            managers = equipment.manager_members()
            assert [m.pk for m in managers] == [lead.pk]

    def it_uses_the_photo_field_for_its_hero_crop():
        assert EquipmentFactory().get_hero_image_field_name() == "photo"

    def describe_access_state():
        def it_is_inactive_member_for_no_member():
            equipment = EquipmentFactory()
            assert equipment.access_state(None) == Equipment.AccessState.INACTIVE_MEMBER

        def it_is_inactive_member_for_an_inactive_member():
            equipment = EquipmentFactory()
            member = MemberFactory(status=Member.Status.FORMER)
            assert equipment.access_state(member) == Equipment.AccessState.INACTIVE_MEMBER

        def it_is_needs_orientation_until_the_gating_type_is_completed():
            orientation_type = OrientationTypeFactory(name="Lathe")
            equipment = EquipmentFactory(required_orientation=orientation_type)
            member = MemberFactory()
            assert equipment.access_state(member) == Equipment.AccessState.NEEDS_ORIENTATION
            _completed_orientation(member, orientation_type)
            assert equipment.access_state(member) == Equipment.AccessState.OK

        def it_is_needs_guild_until_the_member_joins():
            guild = GuildFactory()
            equipment = EquipmentFactory(guild=guild, requires_guild_membership=True)
            member = MemberFactory()
            assert equipment.access_state(member) == Equipment.AccessState.NEEDS_GUILD
            GuildMembershipFactory(guild=guild, member=member)
            assert equipment.access_state(member) == Equipment.AccessState.OK

        def it_ignores_requires_guild_membership_without_a_guild():
            equipment = EquipmentFactory(guild=None, requires_guild_membership=True)
            member = MemberFactory()
            assert equipment.access_state(member) == Equipment.AccessState.OK

        def it_checks_orientation_before_guild_membership():
            guild = GuildFactory()
            orientation_type = OrientationTypeFactory(guild=guild)
            equipment = EquipmentFactory(
                guild=guild, required_orientation=orientation_type, requires_guild_membership=True
            )
            member = MemberFactory()
            assert equipment.access_state(member) == Equipment.AccessState.NEEDS_ORIENTATION

        def describe_with_bulk_sets():
            def it_reads_orientation_from_the_provided_set_without_querying():
                orientation_type = OrientationTypeFactory()
                equipment = EquipmentFactory(required_orientation=orientation_type)
                member = MemberFactory()
                state = equipment.access_state(member, oriented_type_ids={orientation_type.pk}, member_guild_ids=set())
                assert state == Equipment.AccessState.OK
                state = equipment.access_state(member, oriented_type_ids=set(), member_guild_ids=set())
                assert state == Equipment.AccessState.NEEDS_ORIENTATION

            def it_reads_guild_membership_from_the_provided_set():
                guild = GuildFactory()
                equipment = EquipmentFactory(guild=guild, requires_guild_membership=True)
                member = MemberFactory()
                state = equipment.access_state(member, oriented_type_ids=set(), member_guild_ids={guild.pk})
                assert state == Equipment.AccessState.OK
                state = equipment.access_state(member, oriented_type_ids=set(), member_guild_ids=set())
                assert state == Equipment.AccessState.NEEDS_GUILD

    def describe_booking_blockers():
        def it_reports_an_inactive_membership_alone():
            equipment = EquipmentFactory(required_orientation=OrientationTypeFactory())
            member = MemberFactory(status=Member.Status.FORMER)
            assert equipment.booking_blockers(member) == ["Your membership needs to be active to reserve equipment."]

        def it_reports_an_unlinked_viewer_as_inactive():
            equipment = EquipmentFactory()
            assert equipment.booking_blockers(None) == ["Your membership needs to be active to reserve equipment."]

        def it_reports_the_missing_orientation_by_name():
            orientation_type = OrientationTypeFactory(name="Lathe")
            equipment = EquipmentFactory(required_orientation=orientation_type)
            member = MemberFactory()
            assert equipment.booking_blockers(member) == [
                "You need the Lathe orientation before you can book time here."
            ]

        def it_reports_the_missing_guild_membership_by_guild_name():
            guild = GuildFactory(name="Woodshop")
            equipment = EquipmentFactory(guild=guild, requires_guild_membership=True)
            member = MemberFactory()
            assert equipment.booking_blockers(member) == ["Only Woodshop members can book this."]

        def it_stacks_both_blockers_in_order():
            guild = GuildFactory(name="Woodshop")
            orientation_type = OrientationTypeFactory(guild=guild, name="Lathe")
            equipment = EquipmentFactory(
                guild=guild, required_orientation=orientation_type, requires_guild_membership=True
            )
            member = MemberFactory()
            assert equipment.booking_blockers(member) == [
                "You need the Lathe orientation before you can book time here.",
                "Only Woodshop members can book this.",
            ]

        def it_is_empty_when_everything_is_met():
            guild = GuildFactory()
            orientation_type = OrientationTypeFactory(guild=guild)
            equipment = EquipmentFactory(
                guild=guild, required_orientation=orientation_type, requires_guild_membership=True
            )
            member = MemberFactory()
            _completed_orientation(member, orientation_type)
            GuildMembershipFactory(guild=guild, member=member)
            assert equipment.booking_blockers(member) == []


def describe_EquipmentStaffMembership():
    def it_stringifies_as_member_colon_equipment_manager():
        member = MemberFactory(full_legal_name="Dana Reyes")
        equipment = EquipmentFactory(name="CNC Router")
        staff = EquipmentStaffMembershipFactory(equipment=equipment, member=member)
        assert str(staff) == "Dana Reyes: CNC Router manager"

    def it_forbids_the_same_member_twice_on_one_equipment():
        staff = EquipmentStaffMembershipFactory()
        with pytest.raises(IntegrityError):
            EquipmentStaffMembership.objects.create(equipment=staff.equipment, member=staff.member)

    def it_records_the_granting_member():
        granter = MemberFactory()
        staff = EquipmentStaffMembershipFactory(granted_by=granter)
        assert staff.granted_by == granter


def describe_equipment_capability_backfill_migration():
    """The 0161 backfill — mirrors the 0118 precedent: admins keep blanket authority."""

    _migration = importlib.import_module("membership.migrations.0161_equipment_directory")

    def it_grants_equipment_to_existing_admins_only():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        plain = MemberFactory(fog_role=Member.FogRole.MEMBER)

        _migration._backfill_equipment_capability(django_apps, None)

        assert admin.admin_capabilities.filter(capability="equipment").exists()
        assert not plain.admin_capabilities.exists()

    def it_is_idempotent_across_reruns():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        _migration._backfill_equipment_capability(django_apps, None)
        _migration._backfill_equipment_capability(django_apps, None)
        assert admin.admin_capabilities.filter(capability="equipment").count() == 1

    def it_reverse_deletes_exactly_the_equipment_rows():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        admin.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
        _migration._backfill_equipment_capability(django_apps, None)

        _migration._remove_equipment_capability(django_apps, None)

        held = set(admin.admin_capabilities.values_list("capability", flat=True))
        assert held == {"billing_approver"}


def describe_member_can_manage_equipment():
    def it_allows_a_full_admin():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        assert admin.can_manage_equipment(EquipmentFactory()) is True

    def it_allows_an_equipment_capability_holder():
        holder = MemberFactory()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert holder.can_manage_equipment(EquipmentFactory()) is True

    def it_allows_the_owning_guilds_lead():
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        assert lead.can_manage_equipment(EquipmentFactory(guild=guild)) is True

    def it_allows_the_owning_guilds_staff():
        staff = MemberFactory()
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.ORIENTER)
        assert staff.can_manage_equipment(EquipmentFactory(guild=guild)) is True

    def it_allows_an_equipment_staff_row_holder():
        manager = MemberFactory()
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        assert manager.can_manage_equipment(equipment) is True

    def it_denies_a_plain_member():
        plain = MemberFactory()
        assert plain.can_manage_equipment(EquipmentFactory()) is False

    def it_denies_a_lead_of_another_guild():
        lead = MemberFactory()
        GuildFactory(guild_lead=lead)
        assert lead.can_manage_equipment(EquipmentFactory(guild=GuildFactory())) is False

    def it_denies_a_guild_officer_without_a_grant():
        officer = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
        assert officer.can_manage_equipment(EquipmentFactory()) is False


def describe_member_can_create_equipment():
    def it_allows_a_full_admin():
        assert MemberFactory(fog_role=Member.FogRole.ADMIN).can_create_equipment() is True

    def it_allows_an_equipment_capability_holder():
        holder = MemberFactory()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert holder.can_create_equipment() is True

    def it_denies_a_guild_lead_and_a_plain_member():
        lead = MemberFactory()
        GuildFactory(guild_lead=lead)
        assert lead.can_create_equipment() is False
        assert MemberFactory().can_create_equipment() is False


# ── Orientation hours reconcile (equipment-orientation-hours spec §5.3) ─────────────


def describe_holding_seats_on():
    """The seat-holding overlap query both the busy spans and the reservation guard read."""

    def _slot(equipment, offset_hours: int = 0, *, length: int = 60):
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        start = timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=2, hours=offset_hours)
        return OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            starts_at=start,
            ends_at=start + timedelta(minutes=length),
            seats=2,
        )

    def it_returns_each_seat_holding_slot_once():
        equipment = EquipmentFactory()
        slot = _slot(equipment)
        OrientationBookingFactory(slot=slot)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        result = OrientationSlot.objects.holding_seats_on(equipment, slot.starts_at, slot.ends_at)
        assert list(result) == [slot]

    def it_uses_strict_inequalities():
        equipment = EquipmentFactory()
        slot = _slot(equipment)
        OrientationBookingFactory(slot=slot)
        touching_after = OrientationSlot.objects.holding_seats_on(
            equipment, slot.ends_at, slot.ends_at + timedelta(hours=1)
        )
        touching_before = OrientationSlot.objects.holding_seats_on(
            equipment, slot.starts_at - timedelta(hours=1), slot.starts_at
        )
        assert not touching_after.exists()
        assert not touching_before.exists()

    def it_excludes_open_cancelled_and_other_tools_slots():
        equipment = EquipmentFactory()
        open_slot = _slot(equipment)
        cancelled = _slot(equipment, 2)
        OrientationBookingFactory(slot=cancelled)
        cancelled.mark_cancelled(reason="Machine down")
        elsewhere = _slot(EquipmentFactory())
        OrientationBookingFactory(slot=elsewhere)
        window_start = open_slot.starts_at
        window_end = open_slot.starts_at + timedelta(hours=4)
        assert not OrientationSlot.objects.holding_seats_on(equipment, window_start, window_end).exists()


def describe_is_run_by():
    """The set that RUNS orientations is manager_members(): no fog-admin leg, unlike the permission helpers."""

    def _tool():
        return EquipmentFactory(guild=GuildFactory(guild_lead=MemberFactory()))

    def _agrees(equipment, member, expected: bool) -> None:
        assert equipment.is_run_by(member) is expected
        assert (member in equipment.manager_members()) is expected
        # The SQL twin the booking gate reads must agree with the predicate.
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Basics")
        slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type, orienter=member)
        assert (slot in OrientationSlot.objects.bookable()) is expected
        assert slot.is_bookable is expected

    def it_includes_a_staff_row_holder():
        equipment = _tool()
        manager = MemberFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        _agrees(equipment, manager, True)

    def it_includes_the_owning_guilds_lead_and_staff():
        equipment = _tool()
        _agrees(equipment, equipment.guild.guild_lead, True)
        staffer = MemberFactory()
        GuildStaffMembershipFactory(guild=equipment.guild, member=staffer)
        _agrees(equipment, staffer, True)

    def it_includes_an_equipment_capability_holder():
        equipment = _tool()
        holder = MemberFactory()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        _agrees(equipment, holder, True)

    def it_excludes_a_full_admin_without_the_capability_but_not_their_permissions():
        equipment = _tool()
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        _agrees(equipment, admin, False)
        assert admin.can_manage_equipment(equipment) is True  # still edits anyone's hours, acts on requests
        admin.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert equipment.is_run_by(admin) is True

    def it_excludes_a_plain_member_and_another_guilds_lead():
        equipment = _tool()
        _agrees(equipment, MemberFactory(), False)
        other_lead = MemberFactory()
        GuildFactory(guild_lead=other_lead)
        _agrees(equipment, other_lead, False)
