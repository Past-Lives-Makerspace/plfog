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
    EquipmentUnlockingOrientation,
    GuildStaffMembership,
    Member,
    OrientationBooking,
    OrientationSlot,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
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

        def it_never_takes_a_word_a_fixed_route_uses():
            assert EquipmentFactory(name="Bookings").slug == "bookings-2"
            assert EquipmentFactory(name="Add").slug == "add-2"

    def describe_delete_protection():
        def it_protects_a_guild_that_owns_equipment():
            guild = GuildFactory()
            EquipmentFactory(guild=guild)
            with pytest.raises(ProtectedError):
                guild.delete()

        def it_protects_an_orientation_type_that_gates_equipment():
            orientation_type = OrientationTypeFactory()
            EquipmentFactory(unlocking_orientations=[orientation_type])
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

    def describe_orienter_members():
        """The roster the Orientation Schedule lists: this tool's people, not every admin."""

        def it_unions_staff_rows_and_the_owning_guilds_leadership_deduped():
            lead = MemberFactory()
            guild_staff = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            GuildStaffMembershipFactory(guild=guild, member=guild_staff, role=GuildStaffMembership.Role.SECRETARY)
            equipment = EquipmentFactory(guild=guild)
            equipment_manager = MemberFactory()
            EquipmentStaffMembershipFactory(equipment=equipment, member=equipment_manager)

            orienters = equipment.orienter_members()

            assert {m.pk for m in orienters} == {lead.pk, guild_staff.pk, equipment_manager.pk}
            assert len(orienters) == 3

        def it_excludes_capability_holders_that_manager_members_includes():
            # The whole point: a site-wide EQUIPMENT holder manages every tool but is not
            # on any tool's orientation roster until someone adds them to it.
            equipment = EquipmentFactory(guild=None)
            holder = MemberFactory()
            holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
            assert holder.pk in {m.pk for m in equipment.manager_members()}
            assert equipment.orienter_members() == []
            EquipmentStaffMembershipFactory(equipment=equipment, member=holder)
            assert {m.pk for m in equipment.orienter_members()} == {holder.pk}

        def it_counts_a_lead_who_also_holds_a_staff_row_once():
            lead = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            equipment = EquipmentFactory(guild=guild)
            EquipmentStaffMembershipFactory(equipment=equipment, member=lead)
            assert [m.pk for m in equipment.orienter_members()] == [lead.pk]

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
            equipment = EquipmentFactory(unlocking_orientations=[orientation_type])
            member = MemberFactory()
            assert equipment.access_state(member) == Equipment.AccessState.NEEDS_ORIENTATION
            _completed_orientation(member, orientation_type)
            assert equipment.access_state(member) == Equipment.AccessState.OK

        def it_is_ok_on_guild_owned_equipment_for_a_member_outside_the_guild():
            guild = GuildFactory()
            orientation_type = OrientationTypeFactory(guild=guild)
            equipment = EquipmentFactory(guild=guild, unlocking_orientations=[orientation_type])
            member = MemberFactory()
            _completed_orientation(member, orientation_type)
            assert equipment.access_state(member) == Equipment.AccessState.OK

        def describe_with_bulk_sets():
            def it_reads_orientation_from_the_provided_set_without_querying():
                orientation_type = OrientationTypeFactory()
                equipment = EquipmentFactory(unlocking_orientations=[orientation_type])
                member = MemberFactory()
                state = equipment.access_state(member, oriented_type_ids={orientation_type.pk})
                assert state == Equipment.AccessState.OK
                state = equipment.access_state(member, oriented_type_ids=set())
                assert state == Equipment.AccessState.NEEDS_ORIENTATION

    def describe_booking_blockers():
        def it_reports_an_inactive_membership_alone():
            equipment = EquipmentFactory(unlocking_orientations=[OrientationTypeFactory()])
            member = MemberFactory(status=Member.Status.FORMER)
            assert equipment.booking_blockers(member) == ["Your membership needs to be active to reserve equipment."]

        def it_reports_an_unlinked_viewer_as_inactive():
            equipment = EquipmentFactory()
            assert equipment.booking_blockers(None) == ["Your membership needs to be active to reserve equipment."]

        def it_reports_the_missing_orientation_by_name():
            orientation_type = OrientationTypeFactory(name="Lathe")
            equipment = EquipmentFactory(unlocking_orientations=[orientation_type])
            member = MemberFactory()
            assert equipment.booking_blockers(member) == [
                "You need the Lathe orientation before you can reserve this equipment."
            ]

        def it_is_empty_when_everything_is_met():
            guild = GuildFactory()
            orientation_type = OrientationTypeFactory(guild=guild)
            equipment = EquipmentFactory(guild=guild, unlocking_orientations=[orientation_type])
            member = MemberFactory()
            _completed_orientation(member, orientation_type)
            assert equipment.booking_blockers(member) == []

        def it_reports_an_unpaid_late_cancellation_fee_with_its_amount_after_the_access_blockers():
            from tests.billing.factories import LateCancellationFeeFactory

            orientation_type = OrientationTypeFactory(name="Lathe")
            equipment = EquipmentFactory(
                unlocking_orientations=[orientation_type], is_closed=True, closed_message="Down."
            )
            member = MemberFactory()
            LateCancellationFeeFactory(
                orientation_booking=OrientationBookingFactory(member=member, status="cancelled"), amount_cents=3750
            )
            assert equipment.booking_blockers(member) == [
                "You need the Lathe orientation before you can reserve this equipment.",
                "Pay your $37.50 late cancellation fee to book again.",
                "Down.",
            ]

        def it_lifts_the_fee_blocker_once_the_fee_is_paid():
            from billing.models import LateCancellationFee
            from tests.billing.factories import LateCancellationFeeFactory

            equipment = EquipmentFactory()
            member = MemberFactory()
            fee = LateCancellationFeeFactory(
                orientation_booking=OrientationBookingFactory(member=member, status="cancelled")
            )
            assert equipment.booking_blockers(member) == ["Pay your $15.00 late cancellation fee to book again."]
            fee.status = LateCancellationFee.Status.PAID
            fee.save(update_fields=["status"])
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

    def it_allows_a_space_manager():
        holder = MemberFactory()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
        assert holder.can_create_equipment() is True


def describe_member_creatable_equipment_kinds():
    """#502: the role twin of ``membership.permissions.creatable_equipment_kinds``."""

    def it_gives_every_kind_to_a_full_admin():
        assert MemberFactory(fog_role=Member.FogRole.ADMIN).creatable_equipment_kinds() == ["tool", "room", "space"]

    def it_gives_every_kind_to_an_equipment_capability_holder():
        holder = MemberFactory()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert holder.creatable_equipment_kinds() == ["tool", "room", "space"]

    def it_gives_rooms_and_spaces_to_a_space_manager():
        holder = MemberFactory()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_MANAGER)
        assert holder.creatable_equipment_kinds() == ["room", "space"]

    def it_gives_nothing_to_a_guild_lead_or_a_plain_member():
        lead = MemberFactory()
        GuildFactory(guild_lead=lead)
        assert lead.creatable_equipment_kinds() == []
        assert MemberFactory().creatable_equipment_kinds() == []


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
    """The set that RUNS orientations is orienter_members(): this tool's own people.

    No fog-admin leg and no site-wide EQUIPMENT capability leg, unlike the permission
    helpers — authority over every tool is not the same as running orientations on one.
    """

    def _tool():
        return EquipmentFactory(guild=GuildFactory(guild_lead=MemberFactory()))

    def _agrees(equipment, member, expected: bool) -> None:
        assert equipment.is_run_by(member) is expected
        assert (member in equipment.orienter_members()) is expected
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

    def it_excludes_an_equipment_capability_holder_who_is_not_on_this_tool():
        equipment = _tool()
        holder = MemberFactory()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        _agrees(equipment, holder, False)
        # The capability is unchanged as a permission: they still manage every tool.
        assert holder in equipment.manager_members()
        assert holder.can_manage_equipment(equipment) is True
        # A staff row is what puts them on this tool's roster.
        EquipmentStaffMembershipFactory(equipment=equipment, member=holder)
        _agrees(equipment, holder, True)

    def it_excludes_a_full_admin_but_not_their_permissions():
        equipment = _tool()
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        _agrees(equipment, admin, False)
        assert admin.can_manage_equipment(equipment) is True  # still edits anyone's hours, acts on requests
        admin.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert equipment.is_run_by(admin) is False  # authority over every tool is not a slot on this roster
        EquipmentStaffMembershipFactory(equipment=equipment, member=admin)
        assert equipment.is_run_by(admin) is True

    def it_excludes_a_plain_member_and_another_guilds_lead():
        equipment = _tool()
        _agrees(equipment, MemberFactory(), False)
        other_lead = MemberFactory()
        GuildFactory(guild_lead=other_lead)
        _agrees(equipment, other_lead, False)


def describe_any_one_of_several_unlocking_orientations():
    """#656: an item lists zero or more orientations and completing any one unlocks it."""

    def _press() -> tuple[Equipment, object, object]:
        guild = GuildFactory(name="Unlock Printmaking")
        beginner = OrientationTypeFactory(guild=guild, name="Unlock Press Beginner")
        experienced = OrientationTypeFactory(guild=guild, name="Unlock Press Experienced")
        return (
            EquipmentFactory(name="Unlock Press", unlocking_orientations=[beginner, experienced]),
            beginner,
            experienced,
        )

    def it_opens_an_item_with_no_orientations_to_any_active_member():
        equipment = EquipmentFactory()
        member = MemberFactory()
        assert equipment.is_unlocked_for(member) is True
        assert equipment.access_state(member) == Equipment.AccessState.OK

    def it_unlocks_for_a_member_who_completed_either_one():
        equipment, beginner, experienced = _press()
        for completed in (beginner, experienced):
            member = MemberFactory()
            _completed_orientation(member, completed)
            assert equipment.is_unlocked_for(member) is True
            assert equipment.access_state(member) == Equipment.AccessState.OK
            assert equipment.booking_blockers(member) == []

    def it_counts_a_hand_entered_record_for_one_of_them():
        from tests.membership.factories import OrientationRecordFactory

        equipment, _beginner, experienced = _press()
        member = MemberFactory()
        OrientationRecordFactory(member=member, orientation_type=experienced)
        assert equipment.access_state(member) == Equipment.AccessState.OK

    def it_stays_locked_for_a_member_with_none_and_names_every_one():
        equipment, _beginner, _experienced = _press()
        member = MemberFactory()
        assert equipment.access_state(member) == Equipment.AccessState.NEEDS_ORIENTATION
        assert equipment.booking_blockers(member) == [
            "You need one of these orientations before you can reserve this equipment: "
            "Unlock Press Beginner or Unlock Press Experienced."
        ]

    def it_ignores_an_orientation_the_item_does_not_list():
        equipment, _beginner, _experienced = _press()
        member = MemberFactory()
        _completed_orientation(member, OrientationTypeFactory(name="Unlock Unrelated"))
        assert equipment.access_state(member) == Equipment.AccessState.NEEDS_ORIENTATION

    def it_answers_in_one_read_however_many_orientations_it_lists(django_assert_num_queries):
        equipment, _beginner, _experienced = _press()
        member = MemberFactory()
        prefetched = Equipment.objects.prefetch_related("unlocking_orientation_rows").get(pk=equipment.pk)
        with django_assert_num_queries(2):  # completed bookings, then hand-entered records
            assert prefetched.is_unlocked_for(member) is False

    def it_reads_the_bulk_set_without_querying(django_assert_num_queries):
        equipment, beginner, _experienced = _press()
        member = MemberFactory()
        prefetched = Equipment.objects.prefetch_related("unlocking_orientation_rows").get(pk=equipment.pk)
        with django_assert_num_queries(0):
            assert prefetched.is_unlocked_for(member, oriented_type_ids={beginner.pk}) is True
            assert prefetched.is_unlocked_for(member, oriented_type_ids=set()) is False

    def it_protects_each_listed_type_from_deletion():
        _equipment, beginner, experienced = _press()
        for orientation_type in (beginner, experienced):
            with pytest.raises(ProtectedError):
                orientation_type.delete()

    def it_drops_its_rows_with_the_equipment():
        equipment, beginner, _experienced = _press()
        equipment.delete()
        assert not EquipmentUnlockingOrientation.objects.exists()
        assert beginner.gated_equipment.count() == 0

    def it_names_a_row_by_both_ends():
        equipment, beginner, _experienced = _press()
        row = EquipmentUnlockingOrientation.objects.get(equipment=equipment, orientation_type=beginner)
        assert str(row) == "Unlock Press Beginner unlocks Unlock Press"

    def describe_orientation_unlocks():
        def it_gives_each_listed_type_its_book_link_in_display_order():
            equipment, beginner, experienced = _press()
            unlocks = equipment.orientation_unlocks(MemberFactory())
            assert [(u.orientation_type, u.booking, u.url, u.paused) for u in unlocks] == [
                (beginner, None, beginner.orientation_anchor_path(), True),
                (experienced, None, experienced.orientation_anchor_path(), True),
            ]

        def it_offers_a_book_link_only_for_a_type_taking_bookings():
            from tests.membership.factories import GuildOrientationSettingsFactory

            equipment, beginner, experienced = _press()
            GuildOrientationSettingsFactory(guild=beginner.guild)
            experienced.is_active = False
            experienced.save()
            unlocks = equipment.orientation_unlocks(MemberFactory())
            assert [u.paused for u in unlocks] == [False, True]

        def it_carries_the_members_live_booking_for_a_type_in_one_read(django_assert_max_num_queries):
            equipment, beginner, _experienced = _press()
            member = MemberFactory()
            slot = OrientationSlotFactory(guild=beginner.guild, orientation_type=beginner)
            booking = OrientationBookingFactory(member=member, slot=slot, status=OrientationBooking.Status.REQUESTED)
            OrientationBookingFactory(
                member=member,
                slot=OrientationSlotFactory(guild=beginner.guild, orientation_type=beginner),
                status=OrientationBooking.Status.CANCELLED,
            )
            # The detail page's shape: the unlocking rows arrive with each type and its guild (#747).
            prefetched = Equipment.objects.prefetch_related("unlocking_orientation_rows").get(pk=equipment.pk)
            # One read for the live bookings across both types, then the accepting check's guild settings.
            with django_assert_max_num_queries(3):
                unlocks = prefetched.orientation_unlocks(member)
            assert [u.booking for u in unlocks] == [booking, None]
            assert unlocks[0].paused is False


def describe_ways_to_qualify():
    """#747: the orientations are grouped into ways; finishing every one in any one way unlocks the item."""

    def _cnc() -> tuple[Equipment, object, object, object]:
        """The CNC shape: one 6 hour orientation, or both 3 hour sessions."""
        guild = GuildFactory(name="Ways CNC Guild")
        full = OrientationTypeFactory(guild=guild, name="CNC Machine Orientation", duration_minutes=360)
        first = OrientationTypeFactory(guild=guild, name="Session 1 of 2", duration_minutes=180)
        second = OrientationTypeFactory(guild=guild, name="Session 2 of 2", duration_minutes=180)
        cnc = EquipmentFactory(name="CNC Machine", guild=guild)
        cnc.set_unlocking_ways([[full], [first, second]])
        return cnc, full, first, second

    def describe_the_predicate():
        def it_unlocks_a_one_way_pair_only_when_both_are_done():
            guild = GuildFactory()
            first = OrientationTypeFactory(guild=guild, name="Pair One")
            second = OrientationTypeFactory(guild=guild, name="Pair Two")
            equipment = EquipmentFactory()
            equipment.set_unlocking_ways([[first, second]])
            partial = MemberFactory()
            _completed_orientation(partial, first)
            assert equipment.is_unlocked_for(partial) is False
            both = MemberFactory()
            _completed_orientation(both, first)
            _completed_orientation(both, second)
            assert equipment.is_unlocked_for(both) is True

        def it_unlocks_the_cnc_by_either_way():
            cnc, full, first, second = _cnc()
            by_full = MemberFactory()
            _completed_orientation(by_full, full)
            by_sessions = MemberFactory()
            _completed_orientation(by_sessions, first)
            _completed_orientation(by_sessions, second)
            for member in (by_full, by_sessions):
                assert cnc.access_state(member) == Equipment.AccessState.OK
                assert cnc.booking_blockers(member) == []

        def it_keeps_a_member_with_half_a_way_locked():
            cnc, _full, first, _second = _cnc()
            member = MemberFactory()
            _completed_orientation(member, first)
            assert cnc.access_state(member) == Equipment.AccessState.NEEDS_ORIENTATION

        def it_counts_a_hand_entered_record_toward_a_way():
            from tests.membership.factories import OrientationRecordFactory

            cnc, _full, first, second = _cnc()
            member = MemberFactory()
            _completed_orientation(member, first)
            OrientationRecordFactory(member=member, orientation_type=second)
            assert cnc.is_unlocked_for(member) is True

        def it_reads_the_bulk_set_without_querying(django_assert_num_queries):
            cnc, full, first, second = _cnc()
            member = MemberFactory()
            prefetched = Equipment.objects.prefetch_related("unlocking_orientation_rows").get(pk=cnc.pk)
            with django_assert_num_queries(0):
                assert prefetched.is_unlocked_for(member, oriented_type_ids={first.pk}) is False
                assert prefetched.is_unlocked_for(member, oriented_type_ids={first.pk, second.pk}) is True
                assert prefetched.is_unlocked_for(member, oriented_type_ids={full.pk}) is True

        def it_reads_the_ways_in_one_query_bringing_each_type(django_assert_num_queries):
            cnc, *_types = _cnc()
            fresh = Equipment.objects.get(pk=cnc.pk)
            with django_assert_num_queries(1):
                ways = fresh.unlocking_ways()
                assert [[t.name for t in way] for way in ways] == [
                    ["CNC Machine Orientation"],
                    ["Session 1 of 2", "Session 2 of 2"],
                ]
                assert ways[0][0].guild.name == "Ways CNC Guild"

    def describe_the_rows():
        def it_numbers_every_saved_way_and_replaces_the_old_rows():
            cnc, full, first, second = _cnc()
            rows = EquipmentUnlockingOrientation.objects.filter(equipment=cnc)
            assert sorted((row.orientation_type_id, row.way) for row in rows) == sorted(
                [(full.pk, 1), (first.pk, 2), (second.pk, 2)]
            )
            cnc.set_unlocking_ways([[second]])
            assert [(row.orientation_type_id, row.way) for row in rows.all()] == [(second.pk, 1)]

        def it_forgets_a_prefetch_of_the_rows_it_replaced():
            cnc, full, _first, _second = _cnc()
            prefetched = Equipment.objects.prefetch_related("unlocking_orientation_rows").get(pk=cnc.pk)
            assert len(prefetched.unlocking_ways()) == 2
            prefetched.set_unlocking_ways([[full]])
            assert prefetched.unlocking_ways() == [[full]]

        def it_reads_a_row_with_no_way_as_a_way_of_its_own():
            guild = GuildFactory()
            alpha = OrientationTypeFactory(guild=guild, name="Alpha", sort_order=1)
            beta = OrientationTypeFactory(guild=guild, name="Beta", sort_order=2)
            equipment = EquipmentFactory(unlocking_orientations=[beta, alpha])
            assert set(EquipmentUnlockingOrientation.objects.values_list("way", flat=True)) == {None}
            assert equipment.unlocking_ways() == [[alpha], [beta]]

        def it_orders_numbered_ways_before_unnumbered_rows_and_types_within_a_way():
            guild = GuildFactory()
            alpha = OrientationTypeFactory(guild=guild, name="Alpha", sort_order=1)
            beta = OrientationTypeFactory(guild=guild, name="Beta", sort_order=2)
            gamma = OrientationTypeFactory(guild=guild, name="Gamma", sort_order=3)
            equipment = EquipmentFactory()
            EquipmentUnlockingOrientation.objects.create(equipment=equipment, orientation_type=alpha)
            EquipmentUnlockingOrientation.objects.create(equipment=equipment, orientation_type=gamma, way=1)
            EquipmentUnlockingOrientation.objects.create(equipment=equipment, orientation_type=beta, way=1)
            assert equipment.unlocking_ways() == [[beta, gamma], [alpha]]
            assert equipment.unlocking_orientation_list() == [beta, gamma, alpha]

        def it_still_holds_one_orientation_to_one_way():
            cnc, full, _first, _second = _cnc()
            with pytest.raises(IntegrityError):
                EquipmentUnlockingOrientation.objects.create(equipment=cnc, orientation_type=full, way=2)

    def describe_the_sentence():
        def it_names_one_two_and_three_orientations():
            from membership.models import orientation_way_phrase

            assert orientation_way_phrase(["X"]) == "the X"
            assert orientation_way_phrase(["X", "Y"]) == "both X and Y"
            assert orientation_way_phrase(["X", "Y", "Z"]) == "all of X, Y and Z"

        def it_joins_names_as_a_sentence_list():
            from membership.models import join_names

            assert join_names([]) == ""
            assert join_names(["A"]) == "A"
            assert join_names(["A", "B"]) == "A and B"
            assert join_names(["A", "B", "C"]) == "A, B and C"

        def it_joins_mixed_ways_with_or():
            cnc, *_types = _cnc()
            ways = cnc.unlocking_ways()
            assert Equipment.ways_are_grouped(ways) is True
            assert (
                cnc.requirement_phrase(ways) == "the CNC Machine Orientation, or both Session 1 of 2 and Session 2 of 2"
            )
            assert cnc.requirement_sentence(ways) == (
                "Complete the CNC Machine Orientation, or both Session 1 of 2 and Session 2 of 2, "
                "before you reserve the CNC Machine."
            )

        def it_closes_one_way_without_the_list_comma():
            guild = GuildFactory()
            types = [OrientationTypeFactory(guild=guild, name=name) for name in ("A", "B", "C")]
            equipment = EquipmentFactory(name="Kiln")
            equipment.set_unlocking_ways([types])
            assert equipment.requirement_sentence(equipment.unlocking_ways()) == (
                "Complete all of A, B and C before you reserve the Kiln."
            )

        def it_is_not_grouped_when_every_way_is_one_orientation():
            assert Equipment.ways_are_grouped([[object()], [object()]]) is False
            assert Equipment.ways_are_grouped([]) is False

        def it_reads_the_ways_once_for_the_blockers():
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            cnc, _full, first, _second = _cnc()
            member = MemberFactory()
            _completed_orientation(member, first)
            fresh = Equipment.objects.get(pk=cnc.pk)
            with CaptureQueriesContext(connection) as ctx:
                assert len(fresh.booking_blockers(member)) == 1
            row_reads = [
                q for q in ctx.captured_queries if 'FROM "membership_equipmentunlockingorientation"' in q["sql"]
            ]
            assert len(row_reads) == 1

        def it_blocks_a_half_done_member_with_the_sentence():
            cnc, _full, first, _second = _cnc()
            member = MemberFactory()
            _completed_orientation(member, first)
            assert cnc.booking_blockers(member) == [
                "Complete the CNC Machine Orientation, or both Session 1 of 2 and Session 2 of 2, "
                "before you reserve the CNC Machine."
            ]

    def describe_the_progress_line():
        def _ways(*sizes: int) -> list[list[object]]:
            guild = GuildFactory()
            return [
                [OrientationTypeFactory(guild=guild, name=f"W{way}T{index}") for index in range(size)]
                for way, size in enumerate(sizes, start=1)
            ]

        def it_reports_the_started_way():
            ways = _ways(1, 2)
            assert Equipment.way_progress_line(ways, {ways[1][0].pk}) == "W2T0 done. W2T1 to go."

        def it_is_empty_with_no_way_started_or_only_single_ways():
            ways = _ways(1, 2)
            assert Equipment.way_progress_line(ways, set()) == ""
            assert Equipment.way_progress_line(ways, {ways[0][0].pk}) == ""

        def it_skips_a_finished_way():
            ways = _ways(2)
            assert Equipment.way_progress_line(ways, {ways[0][0].pk, ways[0][1].pk}) == ""

        def it_picks_the_way_with_the_fewest_left_and_the_first_on_a_tie():
            ways = _ways(3, 2, 2)
            done = {ways[0][0].pk, ways[1][0].pk, ways[2][0].pk}
            assert Equipment.way_progress_line(ways, done) == "W2T0 done. W2T1 to go."
            done.add(ways[0][1].pk)
            assert Equipment.way_progress_line(ways, done) == "W1T0 and W1T1 done. W1T2 to go."

    def describe_the_banner_rows():
        def it_marks_each_completed_orientation_done_way_by_way():
            cnc, full, first, second = _cnc()
            member = MemberFactory()
            _completed_orientation(member, first)
            unlock_ways = cnc.orientation_unlock_ways(member)
            assert [[(u.orientation_type, u.done) for u in way] for way in unlock_ways] == [
                [(full, False)],
                [(first, True), (second, False)],
            ]
            assert [u.orientation_type for u in cnc.orientation_unlocks(member)] == [full, first, second]

        def it_reads_completion_only_when_a_way_is_grouped(django_assert_num_queries):
            guild = GuildFactory()
            equipment = EquipmentFactory(unlocking_orientations=[OrientationTypeFactory(guild=guild)])
            member = MemberFactory()
            prefetched = Equipment.objects.prefetch_related("unlocking_orientation_rows").get(pk=equipment.pk)
            # The live bookings, then the accepting check's guild settings: no completion read.
            with django_assert_num_queries(2):
                assert [u.done for u in prefetched.orientation_unlocks(member)] == [False]


def describe_duration_labels():
    def it_words_minutes_hours_and_half_hours():
        from membership.models import duration_label

        assert duration_label(45) == "45 minutes"
        assert duration_label(60) == "1 hour"
        assert duration_label(90) == "1.5 hours"
        assert duration_label(360) == "6 hours"

    def it_reads_a_types_length():
        assert OrientationTypeFactory(duration_minutes=180).duration_label == "3 hours"
