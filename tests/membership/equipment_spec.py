"""BDD specs for the Equipment directory models (equipment-reservations spec PR 1).

Covers ``Equipment`` (slug, querysets, access_state, booking_blockers,
manager_members, FK delete protection), ``EquipmentStaffMembership``, the
EQUIPMENT capability backfill migration, and the ``Member`` permission twins.
"""

from __future__ import annotations

import importlib
from datetime import time

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError

from membership.models import (
    AdminCapability,
    Equipment,
    EquipmentStaffMembership,
    GuildStaffMembership,
    Member,
    OrientationAvailability,
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
    OrientationAvailabilityFactory,
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


def describe_Equipment_orientation_hours():
    def _tool_type(**overrides):
        return OrientationTypeFactory(equipment_owned=True, **overrides)

    def _rule(orientation_type, **overrides):
        defaults = {
            "weekday": 5,
            "start_time": time(10, 0),
            "end_time": time(12, 0),
            "slot_minutes": 60,
            "buffer_minutes": 0,
            "seats": 1,
        }
        defaults.update(overrides)
        return OrientationAvailabilityFactory(equipment_owned=True, orientation_type=orientation_type, **defaults)

    def _window(orientation_type, days, **overrides):
        window = {
            "orientation_type": orientation_type,
            "start_time": time(10, 0),
            "end_time": time(12, 0),
            "days": days,
            "slot_minutes": 60,
            "buffer_minutes": 0,
            "seats": 1,
            "is_active": True,
        }
        window.update(overrides)
        return window

    def _open_slot(rule, **overrides):
        return OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=rule.orientation_type,
            availability=rule,
            source=OrientationSlot.Source.GENERATED,
            **overrides,
        )

    def _booked_slot(rule, **booking_overrides):
        slot = _open_slot(rule)
        OrientationBookingFactory(slot=slot, **booking_overrides)
        return slot

    def describe_orientation_hours_windows():
        def it_groups_per_day_rules_into_one_editor_row():
            orientation_type = _tool_type()
            _rule(orientation_type, weekday=5)
            _rule(orientation_type, weekday=6)
            _rule(orientation_type, weekday=1, start_time=time(9, 0), end_time=time(11, 0), slot_minutes=30, seats=2)
            assert orientation_type.equipment.orientation_hours_windows() == [
                {
                    "orientation_type": orientation_type.pk,
                    "start_time": "09:00",
                    "end_time": "11:00",
                    "days": [1],
                    "slot_minutes": 30,
                    "buffer_minutes": 0,
                    "seats": 2,
                    "is_active": True,
                },
                {
                    "orientation_type": orientation_type.pk,
                    "start_time": "10:00",
                    "end_time": "12:00",
                    "days": [5, 6],
                    "slot_minutes": 60,
                    "buffer_minutes": 0,
                    "seats": 1,
                    "is_active": True,
                },
            ]

        def it_shows_the_types_duration_for_a_rule_with_no_slot_length():
            orientation_type = _tool_type(duration_minutes=45)
            _rule(orientation_type, slot_minutes=None)
            assert orientation_type.equipment.orientation_hours_windows()[0]["slot_minutes"] == 45

        def it_ignores_other_equipments_rules():
            orientation_type = _tool_type()
            _rule(_tool_type())
            assert orientation_type.equipment.orientation_hours_windows() == []

    def describe_apply_orientation_hours_windows():
        def it_creates_guildless_orienterless_rules_per_checked_day():
            orientation_type = _tool_type()
            equipment = orientation_type.equipment
            assert equipment.apply_orientation_hours_windows([_window(orientation_type, [5, 6])]) == (0, 0, 0)
            rules = list(OrientationAvailability.objects.for_equipment(equipment).order_by("weekday"))
            assert [rule.weekday for rule in rules] == [5, 6]
            assert all(rule.guild is None and rule.orienter is None for rule in rules)
            assert rules[0].slot_minutes == 60
            assert rules[0].seats == 1

        def it_retires_the_row_for_an_unchecked_day():
            orientation_type = _tool_type()
            saturday = _rule(orientation_type, weekday=5)
            sunday = _rule(orientation_type, weekday=6)
            gone = _open_slot(sunday)
            result = orientation_type.equipment.apply_orientation_hours_windows([_window(orientation_type, [5])])
            assert result == (1, 1, 0)
            assert OrientationAvailability.objects.filter(pk=saturday.pk).exists()
            assert not OrientationAvailability.objects.filter(pk=sunday.pk).exists()
            assert not OrientationSlot.objects.filter(pk=gone.pk).exists()

        def it_retires_every_day_of_a_deleted_window_and_reports_counts():
            orientation_type = _tool_type()
            saturday = _rule(orientation_type, weekday=5)
            sunday = _rule(orientation_type, weekday=6)
            _open_slot(saturday)
            booked = _booked_slot(sunday)
            assert orientation_type.equipment.apply_orientation_hours_windows([]) == (2, 1, 1)
            assert not OrientationAvailability.objects.for_equipment(orientation_type.equipment).exists()
            booked.refresh_from_db()
            assert booked.availability is None
            assert booked.seats == 1

        def it_pauses_and_retires_open_slots_but_keeps_booked_ones():
            orientation_type = _tool_type()
            rule = _rule(orientation_type)
            open_slot = _open_slot(rule)
            booked = _booked_slot(rule)
            result = orientation_type.equipment.apply_orientation_hours_windows(
                [_window(orientation_type, [5], is_active=False)]
            )
            assert result == (0, 1, 1)
            rule.refresh_from_db()
            assert rule.is_active is False
            assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
            booked.refresh_from_db()
            assert booked.availability == rule

        def it_unpauses_without_touching_slots():
            orientation_type = _tool_type()
            rule = _rule(orientation_type, is_active=False)
            open_slot = _open_slot(rule)
            assert orientation_type.equipment.apply_orientation_hours_windows([_window(orientation_type, [5])]) == (
                0,
                0,
                0,
            )
            rule.refresh_from_db()
            assert rule.is_active is True
            assert OrientationSlot.objects.filter(pk=open_slot.pk).exists()

        def it_regrids_when_the_slot_length_changes():
            orientation_type = _tool_type()
            rule = _rule(orientation_type)
            open_slot = _open_slot(rule)
            booked = _booked_slot(rule)
            result = orientation_type.equipment.apply_orientation_hours_windows(
                [_window(orientation_type, [5], slot_minutes=30)]
            )
            assert result == (0, 1, 1)
            rule.refresh_from_db()
            assert rule.slot_minutes == 30
            assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
            assert OrientationSlot.objects.filter(pk=booked.pk).exists()

        def it_treats_a_seats_only_change_as_a_reseat_not_a_regrid():
            orientation_type = _tool_type()
            rule = _rule(orientation_type)
            open_slot = _open_slot(rule, seats=1)
            result = orientation_type.equipment.apply_orientation_hours_windows(
                [_window(orientation_type, [5], seats=2)]
            )
            assert result == (0, 0, 0)
            rule.refresh_from_db()
            assert rule.seats == 2
            open_slot.refresh_from_db()
            assert open_slot.seats == 2

        def it_raises_seats_on_open_and_booked_slots_alike():
            orientation_type = _tool_type()
            rule = _rule(orientation_type, seats=1)
            open_slot = _open_slot(rule, seats=1)
            booked = _open_slot(rule, seats=1)
            OrientationBookingFactory(slot=booked)
            result = orientation_type.equipment.apply_orientation_hours_windows(
                [_window(orientation_type, [5], seats=4)]
            )
            assert result == (0, 0, 0)
            open_slot.refresh_from_db()
            booked.refresh_from_db()
            assert open_slot.seats == 4
            assert booked.seats == 4
            assert booked.seats_remaining == 3

        def it_never_lowers_a_booked_slot_below_its_taken_seats():
            orientation_type = _tool_type()
            rule = _rule(orientation_type, seats=4)
            open_slot = _open_slot(rule, seats=4)
            booked = _open_slot(rule, seats=4)
            OrientationBookingFactory(slot=booked)
            OrientationBookingFactory(slot=booked)
            result = orientation_type.equipment.apply_orientation_hours_windows(
                [_window(orientation_type, [5], seats=1)]
            )
            assert result == (0, 0, 0)
            open_slot.refresh_from_db()
            booked.refresh_from_db()
            assert open_slot.seats == 1
            assert booked.seats == 2

        def it_regrids_when_the_break_changes():
            orientation_type = _tool_type()
            rule = _rule(orientation_type)
            _open_slot(rule)
            result = orientation_type.equipment.apply_orientation_hours_windows(
                [_window(orientation_type, [5], buffer_minutes=15)]
            )
            assert result == (0, 1, 0)
            rule.refresh_from_db()
            assert rule.buffer_minutes == 15

        def it_spares_a_slot_with_a_checkout_hold():
            orientation_type = _tool_type()
            rule = _rule(orientation_type)
            held = _booked_slot(rule, status=OrientationBooking.Status.PENDING_PAYMENT)
            assert orientation_type.equipment.apply_orientation_hours_windows([]) == (1, 0, 1)
            assert OrientationSlot.objects.filter(pk=held.pk).exists()

        def it_never_touches_manual_slots():
            orientation_type = _tool_type()
            _rule(orientation_type)
            manual = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
            orientation_type.equipment.apply_orientation_hours_windows([])
            assert OrientationSlot.objects.filter(pk=manual.pk).exists()

        def it_leaves_an_unchanged_window_alone():
            orientation_type = _tool_type()
            rule = _rule(orientation_type)
            open_slot = _open_slot(rule)
            assert orientation_type.equipment.apply_orientation_hours_windows([_window(orientation_type, [5])]) == (
                0,
                0,
                0,
            )
            assert OrientationAvailability.objects.for_equipment(orientation_type.equipment).get().pk == rule.pk
            assert OrientationSlot.objects.filter(pk=open_slot.pk).exists()
