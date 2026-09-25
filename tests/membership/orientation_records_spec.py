"""BDD specs for hand-recorded orientations (issue #465): the model, the one resolver, the gates.

An admin records an orientation a member completed outside the booking flow. The record
and a completed booking are read by one resolver, ``Member.completed_orientation_type_ids``,
so ``is_oriented_for`` (guild join gating), ``is_oriented_for_type`` (equipment, slot
booking) and the bulk page readers all flip on a record alone and close again when it
is removed. Recording is silent: no email, no event, one SiteActivity row.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from core.models import EventDelivery, Notification, SiteActivity
from membership import equipment as equipment_service
from membership.models import Equipment, Member, OrientationError, OrientationRecord
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationRecordFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _linked_member(username: str) -> Member:
    """An ACTIVE member with a linked User (the reservation engine notifies through it)."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return member


def _completed_booking(member: Member, orientation_type, *, days_ago: int = 3):
    slot = OrientationSlotFactory(
        guild=orientation_type.guild,
        orientation_type=orientation_type,
        starts_at=timezone.now() - timedelta(days=days_ago),
        ends_at=timezone.now() - timedelta(days=days_ago) + timedelta(hours=1),
    )
    booking = OrientationBookingFactory(slot=slot, member=member)
    booking.mark_completed()
    return booking


def describe_OrientationRecord():
    def it_names_the_member_the_type_and_the_day_in_str():
        member = MemberFactory(full_legal_name="Rae Rivers")
        guild = GuildFactory(name="Woodshop")
        record = OrientationRecordFactory(
            member=member,
            orientation_type=OrientationTypeFactory(guild=guild, name="Shop Basics"),
            completed_on=timezone.localdate() - timedelta(days=1),
        )
        text = str(record)
        assert "Rae Rivers" in text
        assert "Woodshop — Shop Basics" in text
        assert (timezone.localdate() - timedelta(days=1)).isoformat() in text

    def it_allows_one_record_per_member_per_type():
        record = OrientationRecordFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            OrientationRecord.objects.create(
                member=record.member, orientation_type=record.orientation_type, completed_on=timezone.localdate()
            )

    def it_protects_a_type_with_recorded_history():
        record = OrientationRecordFactory()
        with pytest.raises(ProtectedError):
            record.orientation_type.delete()

    def describe_record():
        def it_creates_the_row_and_logs_the_admin_as_actor():
            admin = User.objects.create_user(username="rec-admin", email="rec-admin@example.com", password="x")
            member = MemberFactory()
            orienter = MemberFactory(full_legal_name="Dana Orienter")
            guild = GuildFactory(name="Metalshop")
            lathe = OrientationTypeFactory(guild=guild, name="Lathe")
            day = timezone.localdate() - timedelta(days=10)
            record = OrientationRecord.record(
                member, lathe, completed_on=day, oriented_by=orienter, note="In person", recorded_by=admin
            )
            record.refresh_from_db()
            assert (record.member, record.orientation_type, record.completed_on) == (member, lathe, day)
            assert (record.oriented_by, record.note, record.recorded_by) == (orienter, "In person", admin)
            row = SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_RECORDED)
            assert row.actor == admin
            assert row.target == member
            assert row.payload == {"orientation_type": "Lathe", "owner": "Metalshop"}

        def it_defaults_the_optional_fields():
            member = MemberFactory()
            record = OrientationRecord.record(member, OrientationTypeFactory(), completed_on=timezone.localdate())
            assert (record.oriented_by, record.note, record.recorded_by) == (None, "", None)
            assert SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_RECORDED).actor is None

        def it_sends_nothing_and_emits_nothing():
            member = MemberFactory()
            mail.outbox.clear()
            with mock.patch("core.events.emit.emit") as emit:
                OrientationRecord.record(member, OrientationTypeFactory(), completed_on=timezone.localdate())
            emit.assert_not_called()
            assert mail.outbox == []
            assert not Notification.objects.exists()
            assert not EventDelivery.objects.exists()
            assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_COMPLETED).exists()

    def describe_remove():
        def it_deletes_the_row_and_logs_it():
            admin = User.objects.create_user(username="rm-admin", email="rm-admin@example.com", password="x")
            guild = GuildFactory(name="Textiles")
            record = OrientationRecordFactory(orientation_type=OrientationTypeFactory(guild=guild, name="Loom"))
            member = record.member
            record.remove(removed_by=admin)
            assert not OrientationRecord.objects.filter(member=member).exists()
            row = SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_RECORD_REMOVED)
            assert row.actor == admin
            assert row.target == member
            assert row.payload == {"orientation_type": "Loom", "owner": "Textiles"}

        def it_never_touches_a_booking():
            member = MemberFactory()
            guild = GuildFactory()
            basics = OrientationTypeFactory(guild=guild, name="Shop Basics")
            lathe = OrientationTypeFactory(guild=guild, name="Lathe")
            booking = _completed_booking(member, basics)
            OrientationRecordFactory(member=member, orientation_type=lathe).remove(removed_by=None)
            booking.refresh_from_db()
            assert booking.is_completed is True
            assert member.is_oriented_for_type(basics) is True
            assert member.is_oriented_for_type(lathe) is False

        def it_sends_nothing_and_emits_nothing():
            record = OrientationRecordFactory()
            mail.outbox.clear()
            with mock.patch("core.events.emit.emit") as emit:
                record.remove(removed_by=None)
            emit.assert_not_called()
            assert mail.outbox == []
            assert not Notification.objects.exists()
            assert not EventDelivery.objects.exists()

    def describe_recorded_by_name():
        def it_is_unknown_without_a_recording_user():
            assert OrientationRecordFactory(recorded_by=None).recorded_by_name == "Unknown"

        def it_names_the_admins_member():
            MembershipPlanFactory()
            admin = User.objects.create_user(username="named-admin", email="named@example.com", password="x")
            admin.member.full_legal_name = "Named Admin"
            admin.member.save(update_fields=["full_legal_name"])
            record = OrientationRecord.objects.with_related().get(pk=OrientationRecordFactory(recorded_by=admin).pk)
            assert record.recorded_by_name == "Named Admin"

        def it_falls_back_to_the_email_for_a_user_with_no_member():
            MembershipPlanFactory()
            admin = User.objects.create_user(username="bare-admin", email="bare@example.com", password="x")
            Member.objects.filter(user=admin).delete()
            record = OrientationRecord.objects.with_related().get(pk=OrientationRecordFactory(recorded_by=admin).pk)
            assert record.recorded_by_name == "bare@example.com"


def describe_completed_orientation_type_ids():
    def it_unions_completed_bookings_and_records():
        member = MemberFactory()
        guild = GuildFactory()
        booked = OrientationTypeFactory(guild=guild, name="Booked")
        recorded = OrientationTypeFactory(guild=guild, name="Recorded")
        pending = OrientationTypeFactory(guild=guild, name="Pending")
        _completed_booking(member, booked)
        OrientationRecordFactory(member=member, orientation_type=recorded)
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, orientation_type=pending), member=member)
        assert member.completed_orientation_type_ids() == {booked.pk, recorded.pk}

    def it_is_empty_for_a_member_with_nothing():
        assert MemberFactory().completed_orientation_type_ids() == set()

    def it_narrows_to_a_list_of_types():
        member = MemberFactory()
        guild = GuildFactory()
        booked = OrientationTypeFactory(guild=guild, name="Booked")
        recorded = OrientationTypeFactory(guild=guild, name="Recorded")
        _completed_booking(member, booked)
        OrientationRecordFactory(member=member, orientation_type=recorded)
        assert member.completed_orientation_type_ids([booked]) == {booked.pk}
        assert member.completed_orientation_type_ids([recorded]) == {recorded.pk}
        assert member.completed_orientation_type_ids([]) == set()

    def it_narrows_to_a_queryset_of_types():
        member = MemberFactory()
        woodshop = GuildFactory()
        metalshop = GuildFactory()
        wood = OrientationTypeFactory(guild=woodshop, name="Wood")
        metal = OrientationTypeFactory(guild=metalshop, name="Metal")
        OrientationRecordFactory(member=member, orientation_type=wood)
        _completed_booking(member, metal)
        assert member.completed_orientation_type_ids(woodshop.orientation_types.all()) == {wood.pk}
        assert member.completed_orientation_type_ids(metalshop.orientation_types.all()) == {metal.pk}

    def it_is_scoped_to_the_member():
        other = MemberFactory()
        record = OrientationRecordFactory()
        assert other.completed_orientation_type_ids([record.orientation_type]) == set()


def describe_gates_on_a_record_alone():
    def describe_is_oriented_for():
        def it_opens_on_a_record_and_closes_when_it_is_removed():
            guild = GuildFactory()
            member = MemberFactory()
            assert member.is_oriented_for(guild) is False
            record = OrientationRecordFactory(member=member, orientation_type=OrientationTypeFactory(guild=guild))
            assert member.is_oriented_for(guild) is True
            record.remove(removed_by=None)
            assert member.is_oriented_for(guild) is False

        def it_stays_guild_specific():
            woodshop = GuildFactory()
            metalshop = GuildFactory()
            member = MemberFactory()
            OrientationRecordFactory(member=member, orientation_type=OrientationTypeFactory(guild=woodshop))
            assert member.is_oriented_for(woodshop) is True
            assert member.is_oriented_for(metalshop) is False

        def it_counts_a_retired_type():
            guild = GuildFactory()
            member = MemberFactory()
            retired = OrientationTypeFactory(guild=guild, name="Old Basics", is_active=False)
            OrientationRecordFactory(member=member, orientation_type=retired)
            assert member.is_oriented_for(guild) is True

        def it_still_opens_on_a_completed_booking():
            guild = GuildFactory()
            member = MemberFactory()
            _completed_booking(member, OrientationTypeFactory(guild=guild))
            assert member.is_oriented_for(guild) is True

    def describe_is_oriented_for_type():
        def it_opens_on_a_record_and_closes_when_it_is_removed():
            orientation_type = OrientationTypeFactory(equipment_owned=True)
            member = MemberFactory()
            assert member.is_oriented_for_type(orientation_type) is False
            record = OrientationRecordFactory(member=member, orientation_type=orientation_type)
            assert member.is_oriented_for_type(orientation_type) is True
            record.remove(removed_by=None)
            assert member.is_oriented_for_type(orientation_type) is False

        def it_stays_type_specific():
            guild = GuildFactory()
            member = MemberFactory()
            basics = OrientationTypeFactory(guild=guild, name="Shop Basics")
            lathe = OrientationTypeFactory(guild=guild, name="Lathe")
            OrientationRecordFactory(member=member, orientation_type=basics)
            assert member.is_oriented_for_type(basics) is True
            assert member.is_oriented_for_type(lathe) is False

    def it_refuses_a_booking_for_a_recorded_type():
        guild = GuildFactory()
        member = MemberFactory()
        basics = OrientationTypeFactory(guild=guild, name="Shop Basics")
        OrientationRecordFactory(member=member, orientation_type=basics)
        slot = OrientationSlotFactory(guild=guild, orientation_type=basics)
        with pytest.raises(OrientationError, match="already completed this orientation"):
            slot.book(member)

    def describe_equipment():
        def _gated_tool():
            orientation_type = OrientationTypeFactory(name="Lathe")
            equipment = EquipmentFactory(required_orientation=orientation_type)
            day = timezone.localdate() + timedelta(days=2)
            EquipmentHoursFactory(
                equipment=equipment, weekday=day.weekday(), start_time=time(9, 0), end_time=time(17, 0)
            )
            return equipment, orientation_type, day

        def it_reads_all_set_after_a_record():
            equipment, orientation_type, _day = _gated_tool()
            member = MemberFactory()
            assert equipment.access_state(member) == Equipment.AccessState.NEEDS_ORIENTATION
            assert equipment.booking_blockers(member) == [
                "You need the Lathe orientation before you can book time here."
            ]
            OrientationRecordFactory(member=member, orientation_type=orientation_type)
            assert equipment.access_state(member) == Equipment.AccessState.OK
            assert equipment.booking_blockers(member) == []
            # The bulk (index page) path reads the same resolver.
            oriented = member.completed_orientation_type_ids()
            assert equipment.access_state(member, oriented_type_ids=oriented, member_guild_ids=set()) == "ok"

        def it_lets_the_member_reserve_after_a_record():
            equipment, orientation_type, day = _gated_tool()
            member = _linked_member("rec-reserver")
            starts_at = timezone.make_aware(datetime.combine(day, time(10, 0)))
            with pytest.raises(Exception, match="Lathe orientation before you can book"):
                equipment_service.reserve(equipment, member, starts_at, 60)
            OrientationRecordFactory(member=member, orientation_type=orientation_type)
            reservation = equipment_service.reserve(equipment, member, starts_at, 60)
            assert reservation.member == member
            assert reservation.equipment == equipment
