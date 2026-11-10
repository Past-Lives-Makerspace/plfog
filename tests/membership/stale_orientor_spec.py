"""BDD specs for #1129: orientation times leave with the Orientor who stops running them.

One predicate (``Guild.orientor_ids`` / ``Equipment.orientor_ids``: an Active member who
leads or staffs the guild, or runs the tool) decides generation, bookability and the
retirement that runs on a lead change, a member leaving Active, and the daily job.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone

from core.events.discord_leadership_directory import _SyncWhenCommitted
from core.models import Notification
from membership import orientations
from membership.models import (
    GuildStaffMembership,
    Member,
    OrientationAvailability,
    OrientationAvailabilityBlock,
    OrientationBooking,
    OrientationError,
    OrientationSlot,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    OrientationAvailabilityBlockFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _guild(lead: Member | None = None) -> object:
    guild = GuildFactory(guild_lead=lead)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    return guild


def _staff(guild: object, member: Member) -> None:
    GuildStaffMembershipFactory(guild=guild, member=member, role=GuildStaffMembership.Role.ORIENTER)


def _hours(guild: object, orienter: Member) -> tuple[OrientationAvailability, list[OrientationSlot]]:
    """A personal weekly rule in ``guild`` with its generated future slots."""
    rule = OrientationAvailabilityFactory(guild=guild, orienter=orienter)
    orientations.generate_slots(guild=guild)
    slots = list(rule.slots.order_by("starts_at"))
    assert slots, "the rule should have generated future slots"
    return rule, slots


def _tool_hours(equipment: object, orienter: Member) -> OrientationAvailability:
    """A personal equipment rule on ``equipment`` with its generated slots."""
    rule = OrientationAvailabilityFactory(
        equipment_owned=True,
        orientation_type=OrientationTypeFactory(equipment_owned=True, equipment=equipment, guild=None),
        orienter=orienter,
    )
    orientations.generate_slots(equipment=equipment)
    assert rule.slots.exists()
    return rule


def _manual_slot(guild: object, orienter: Member) -> OrientationSlot:
    return OrientationSlotFactory(guild=guild, orienter=orienter, source=OrientationSlot.Source.MANUAL)


def _bookable_pks() -> set[int]:
    return set(OrientationSlot.objects.bookable().values_list("pk", flat=True))


def _leave_active(member: Member, status: str) -> None:
    member.status = status
    member.save()


def describe_orientor_ids():
    def it_counts_an_active_lead_and_staff_and_drops_anyone_not_active():
        lead = MemberFactory()
        staffer = MemberFactory()
        suspended = MemberFactory(status=Member.Status.SUSPENDED)
        guild = _guild(lead)
        _staff(guild, staffer)
        _staff(guild, suspended)
        assert guild.orientor_ids() == {lead.pk, staffer.pk}

    def it_counts_a_tools_active_orientors_including_its_guilds_leadership():
        lead = MemberFactory()
        manager = MemberFactory()
        former = MemberFactory(status=Member.Status.FORMER)
        equipment = EquipmentFactory(guild=_guild(lead))
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        EquipmentStaffMembershipFactory(equipment=equipment, member=former)
        assert equipment.orientor_ids() == {lead.pk, manager.pk}


def describe_replacing_or_clearing_a_lead():
    def it_hides_the_old_leads_times_at_once_and_keeps_their_data_until_the_nightly_job():
        old_lead = MemberFactory()
        guild = _guild(old_lead)
        rule, slots = _hours(guild, old_lead)
        window = OrientationAvailabilityBlockFactory(guild=guild, orienter=old_lead)

        guild.assign_lead(MemberFactory())

        assert OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert OrientationSlot.objects.filter(pk__in=[slot.pk for slot in slots]).count() == len(slots)
        assert not _bookable_pks() & {slot.pk for slot in slots}
        assert window.pk not in set(OrientationAvailabilityBlock.objects.offered().values_list("pk", flat=True))

    def it_keeps_every_time_when_the_old_lead_is_kept_on_as_staff_through_the_staff_tab():
        # The Staff tab refuses to add the sitting lead, so the UI order is replace, then add.
        old_lead = MemberFactory()
        guild = _guild(old_lead)
        rule, slots = _hours(guild, old_lead)
        with pytest.raises(ValueError):
            guild.add_staff(old_lead, role=GuildStaffMembership.Role.ORIENTER)

        guild.assign_lead(MemberFactory())
        guild.add_staff(old_lead, role=GuildStaffMembership.Role.ORIENTER)
        orientations.retire_stale_orientor_hours()

        assert OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert {slot.pk for slot in slots} <= _bookable_pks()

    def it_lets_the_nightly_job_retire_a_replaced_lead_who_was_not_kept_on():
        old_lead = MemberFactory()
        guild = _guild(old_lead)
        rule, slots = _hours(guild, old_lead)
        booked = slots[0]
        OrientationBookingFactory(slot=booked)
        manual = _manual_slot(guild, old_lead)
        guild.assign_lead(MemberFactory())

        orientations.retire_stale_orientor_hours()

        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert not OrientationSlot.objects.filter(pk__in=[slot.pk for slot in slots[1:]]).exists()
        assert not OrientationSlot.objects.filter(pk=manual.pk).exists()
        # The booked time stays, so a lead can move or cancel it.
        assert OrientationSlot.objects.filter(pk=booked.pk, is_cancelled=False).exists()

    def it_retires_their_hours_on_the_guilds_tools_but_not_in_a_guild_they_still_run():
        old_lead = MemberFactory()
        guild = _guild(old_lead)
        elsewhere = _guild(old_lead)
        tool_rule = _tool_hours(EquipmentFactory(guild=guild), old_lead)
        kept_rule, _slots = _hours(elsewhere, old_lead)
        guild.assign_lead(MemberFactory())

        orientations.retire_stale_orientor_hours()

        assert not OrientationAvailability.objects.filter(pk=tool_rule.pk).exists()
        assert OrientationAvailability.objects.filter(pk=kept_rule.pk).exists()

    def it_hides_a_cleared_leads_times_and_the_job_retires_them_unless_they_are_staff():
        gone = MemberFactory()
        guild = _guild(gone)
        rule, slots = _hours(guild, gone)
        stays = MemberFactory()
        other = _guild(stays)
        _staff(other, stays)
        kept, _kept_slots = _hours(other, stays)

        assert guild.clear_lead() == gone
        other.clear_lead()
        assert not _bookable_pks() & {slot.pk for slot in slots}
        orientations.retire_stale_orientor_hours()

        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert OrientationAvailability.objects.filter(pk=kept.pk).exists()

    def it_clears_a_guild_with_no_lead_quietly():
        assert _guild().clear_lead() is None

    def it_hides_the_times_through_the_set_guild_lead_command():
        old_lead = MemberFactory()
        guild = _guild(old_lead)
        _rule, slots = _hours(guild, old_lead)
        call_command("set_guild_lead", "--guild", str(guild.pk), "--clear", stdout=StringIO())
        assert not _bookable_pks() & {slot.pk for slot in slots}


def describe_a_member_leaving_active():
    @pytest.mark.parametrize("status", [Member.Status.FORMER, Member.Status.SUSPENDED])
    def it_loses_their_open_future_times_everywhere(status, django_capture_on_commit_callbacks):
        orientor = MemberFactory()
        guild = _guild(orientor)
        other = _guild()
        _staff(other, orientor)
        rule, slots = _hours(guild, orientor)
        other_rule, _other_slots = _hours(other, orientor)
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=orientor)
        tool_rule = _tool_hours(equipment, orientor)
        manual = _manual_slot(other, orientor)
        window = OrientationAvailabilityBlockFactory(guild=other, orienter=orientor)
        booked = slots[0]
        booking = OrientationBookingFactory(slot=booked)

        with django_capture_on_commit_callbacks(execute=True):
            _leave_active(orientor, status)

        assert not OrientationAvailability.objects.filter(pk__in=[rule.pk, other_rule.pk, tool_rule.pk]).exists()
        assert not OrientationSlot.objects.filter(orienter=orientor).exclude(pk=booked.pk).exists()
        assert not OrientationSlot.objects.filter(pk=manual.pk).exists()
        assert not OrientationAvailabilityBlock.objects.filter(pk=window.pk).exists()
        # A live booking keeps its time and its row.
        assert OrientationSlot.objects.filter(pk=booked.pk, is_cancelled=False).exists()
        assert OrientationBooking.objects.get(pk=booking.pk).status == booking.status

    def it_waits_for_the_status_change_to_commit(django_capture_on_commit_callbacks):
        orientor = MemberFactory()
        rule, _slots = _hours(_guild(orientor), orientor)
        with django_capture_on_commit_callbacks() as callbacks:
            _leave_active(orientor, Member.Status.FORMER)
            assert OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert len(callbacks) == 1

    def it_keeps_the_status_change_when_the_sweep_fails(django_capture_on_commit_callbacks):
        orientor = MemberFactory()
        _hours(_guild(orientor), orientor)
        with patch.object(orientations, "retire_stale_orientor_hours", side_effect=RuntimeError("boom")):
            with django_capture_on_commit_callbacks(execute=True):
                _leave_active(orientor, Member.Status.FORMER)
        assert Member.objects.get(pk=orientor.pk).status == Member.Status.FORMER

    def it_sweeps_only_when_the_save_writes_the_status(django_capture_on_commit_callbacks):
        orientor = MemberFactory()
        with django_capture_on_commit_callbacks() as skipped:
            orientor.status = Member.Status.FORMER
            orientor.full_legal_name = "Renamed Person"
            orientor.save(update_fields=["full_legal_name"])
        assert skipped == []
        written = Member.objects.get(pk=orientor.pk)
        with django_capture_on_commit_callbacks() as scheduled:
            written.status = Member.Status.FORMER
            written.save(update_fields=["status"])
        assert len(scheduled) == 1

    def it_clears_the_teaching_bell_only_when_the_save_writes_the_status():
        member = MemberFactory(teaching_applied_at=timezone.now())
        reviewer = User.objects.create_user(username="bell-reviewer", email="bell-reviewer@example.com")
        bell = Notification.objects.create(
            user=reviewer,
            trigger="instructor_application_received",
            title="Teaching application",
            body="A member applied to teach.",
            url=member.request_bell_path,
        )
        member.status = Member.Status.FORMER
        member.save(update_fields=["full_legal_name"])
        bell.refresh_from_db()
        assert bell.read_at is None
        written = Member.objects.get(pk=member.pk)
        written.status = Member.Status.FORMER
        written.save(update_fields=["status"])
        bell.refresh_from_db()
        assert bell.read_at is not None

    def it_keeps_everything_on_an_ordinary_save(django_capture_on_commit_callbacks):
        orientor = MemberFactory()
        guild = _guild(orientor)
        rule, _slots = _hours(guild, orientor)
        with django_capture_on_commit_callbacks() as callbacks:
            orientor.full_legal_name = "Renamed Person"
            orientor.save()
        # A lead's rename syncs the Discord leadership directory post (#1149); nothing else is queued.
        assert [cb for cb in callbacks if not isinstance(cb, _SyncWhenCommitted)] == []
        assert OrientationAvailability.objects.filter(pk=rule.pk).exists()

    def it_leaves_other_orientors_alone(django_capture_on_commit_callbacks):
        orientor = MemberFactory()
        colleague = MemberFactory()
        guild = _guild(orientor)
        _staff(guild, colleague)
        kept, _slots = _hours(guild, colleague)
        with django_capture_on_commit_callbacks(execute=True):
            _leave_active(orientor, Member.Status.FORMER)
        assert OrientationAvailability.objects.filter(pk=kept.pk).exists()


def describe_the_daily_job():
    def it_removes_stale_rules_and_open_times_and_leaves_booked_ones():
        lead = MemberFactory()
        departed = MemberFactory()
        guild = _guild(lead)
        _staff(guild, departed)
        stale_rule, stale_slots = _hours(guild, departed)
        current_rule, current_slots = _hours(guild, lead)
        booked = stale_slots[0]
        OrientationBookingFactory(slot=booked)
        # A Django admin removal: the staff row goes without the Staff tab's retirement.
        GuildStaffMembership.objects.filter(guild=guild, member=departed).delete()

        out = StringIO()
        call_command("retire_stale_orientor_hours", stdout=out)

        assert not OrientationAvailability.objects.filter(pk=stale_rule.pk).exists()
        assert set(OrientationSlot.objects.filter(orienter=departed).values_list("pk", flat=True)) == {booked.pk}
        assert OrientationSlot.objects.filter(pk=booked.pk, is_cancelled=False).exists()
        assert OrientationAvailability.objects.filter(pk=current_rule.pk).exists()
        assert OrientationSlot.objects.filter(orienter=lead).count() == len(current_slots)
        assert f"Retired 1 hours row(s) and {len(stale_slots) - 1} open time(s)" in out.getvalue()
        assert "kept 1 booked time(s)" in out.getvalue()

    def it_catches_a_status_change_that_skipped_save():
        orientor = MemberFactory()
        guild = _guild(orientor)
        rule, _slots = _hours(guild, orientor)
        Member.objects.filter(pk=orientor.pk).update(status=Member.Status.FORMER)

        sweep = orientations.retire_stale_orientor_hours()

        assert sweep.rules == 1
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()

    def it_catches_a_lead_changed_in_django_admin_on_a_tool_too():
        old_lead = MemberFactory()
        guild = _guild(old_lead)
        tool_rule = _tool_hours(EquipmentFactory(guild=guild), old_lead)
        manual_tool_slot = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=tool_rule.orientation_type,
            orienter=old_lead,
            source=OrientationSlot.Source.MANUAL,
        )
        type(guild).objects.filter(pk=guild.pk).update(guild_lead=MemberFactory())

        orientations.retire_stale_orientor_hours()

        assert not OrientationAvailability.objects.filter(pk=tool_rule.pk).exists()
        assert not OrientationSlot.objects.filter(pk=manual_tool_slot.pk).exists()

    def it_cancels_a_stale_posted_time_with_booking_history_instead_of_deleting_it():
        departed = MemberFactory(status=Member.Status.FORMER)
        guild = _guild()
        slot = _manual_slot(guild, departed)
        declined = OrientationBookingFactory(slot=slot)
        declined.decline()

        orientations.retire_stale_orientor_hours()

        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert OrientationBooking.objects.filter(pk=declined.pk).exists()

    def it_retires_a_stale_window_that_holds_a_booking_without_deleting_it():
        departed = MemberFactory()
        guild = _guild()
        window = OrientationAvailabilityBlockFactory(guild=guild, orienter=departed)
        orientation_type = OrientationTypeFactory(guild=guild, duration_minutes=60)
        booking = orientations.request_block_orientation(
            window, MemberFactory(), window.starts_at, orientation_type=orientation_type
        )
        GuildStaffMembership.objects.filter(guild=guild, member=departed).delete()

        sweep = orientations.retire_stale_orientor_hours()

        window.refresh_from_db()
        assert window.is_cancelled is True
        assert sweep.kept == 1
        assert OrientationBooking.objects.filter(pk=booking.pk).exists()

    def it_retires_an_orphaned_generated_time_once_its_booking_is_cancelled():
        orientor = MemberFactory()
        guild = _guild(orientor)
        rule, slots = _hours(guild, orientor)
        slot = slots[0]
        booking = OrientationBookingFactory(slot=slot)
        Member.objects.filter(pk=orientor.pk).update(status=Member.Status.FORMER)
        orientations.retire_stale_orientor_hours()
        slot.refresh_from_db()
        assert slot.availability_id is None  # the rule went; the booked time stayed, detached
        booking.cancel()
        untouched = OrientationSlotFactory(
            guild=guild, orienter=orientor, source=OrientationSlot.Source.GENERATED, availability=None
        )

        sweep = orientations.retire_stale_orientor_hours()

        slot.refresh_from_db()
        assert slot.is_cancelled is True  # cancelled, not deleted: the booking's history stays
        assert OrientationBooking.objects.filter(pk=booking.pk).exists()
        assert not OrientationSlot.objects.filter(pk=untouched.pk).exists()
        assert sweep.times == 2
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()

    def it_leaves_shared_slots_and_a_bookings_own_times_alone():
        departed = MemberFactory(status=Member.Status.FORMER)
        guild = _guild()
        shared_rule = OrientationAvailabilityFactory(guild=guild)
        orientations.generate_slots(guild=guild)
        own = OrientationSlotFactory(guild=guild, orienter=departed, source=OrientationSlot.Source.CUSTOM)
        past = _manual_slot(guild, departed)
        OrientationSlot.objects.filter(pk=past.pk).update(starts_at=timezone.now() - timedelta(days=1))

        sweep = orientations.retire_stale_orientor_hours()

        assert sweep.times == 0
        assert OrientationAvailability.objects.filter(pk=shared_rule.pk).exists()
        assert OrientationSlot.objects.filter(pk__in=[own.pk, past.pk]).count() == 2

    def it_runs_in_the_nightly_window_before_slot_generation():
        from core.scheduled_jobs import JOBS_BY_KEY, SCHEDULED_JOBS, Cadence

        keys = [job.key for job in SCHEDULED_JOBS]
        assert JOBS_BY_KEY["retire_stale_orientor_hours"].cadence == Cadence.DAILY
        assert keys.index("retire_stale_orientor_hours") < keys.index("generate_orientation_slots")


def describe_bookability():
    @pytest.fixture
    def lapsed():
        orientor = MemberFactory()
        guild = _guild(orientor)
        slot = _manual_slot(guild, orientor)
        Member.objects.filter(pk=orientor.pk).update(status=Member.Status.SUSPENDED)
        slot.refresh_from_db()
        return slot

    def it_hides_a_lapsed_orientors_guild_slot_before_the_job_runs(lapsed):
        assert lapsed.pk not in _bookable_pks()
        assert lapsed.is_bookable is False

    def it_refuses_a_booking_on_it(lapsed):
        with pytest.raises(OrientationError):
            orientations.request_orientation(lapsed, MemberFactory())
        assert not lapsed.bookings.exists()

    def it_refuses_a_checkout_on_it(lapsed):
        with pytest.raises(OrientationError):
            lapsed.ensure_bookable_for(MemberFactory())

    def it_refuses_a_staffer_seating_someone_on_it(lapsed):
        with pytest.raises(OrientationError):
            orientations.request_orientation(lapsed, MemberFactory(), by_staff=True)

    def it_keeps_an_active_orientors_slot_bookable():
        orientor = MemberFactory()
        slot = _manual_slot(_guild(orientor), orientor)
        assert slot.pk in _bookable_pks()
        assert slot.is_bookable is True

    def it_hides_a_lapsed_tool_orientors_slot():
        manager = MemberFactory()
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        slot = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=OrientationTypeFactory(equipment_owned=True, equipment=equipment, guild=None),
            orienter=manager,
        )
        assert slot.pk in _bookable_pks()
        Member.objects.filter(pk=manager.pk).update(status=Member.Status.FORMER)
        slot.refresh_from_db()
        assert slot.pk not in _bookable_pks()
        assert slot.is_bookable is False

    def it_stops_offering_a_lapsed_orientors_window_and_refuses_a_booking_in_it():
        window = OrientationAvailabilityBlockFactory()
        orientation_type = OrientationTypeFactory(guild=window.guild, duration_minutes=60)
        assert window.pk in set(OrientationAvailabilityBlock.objects.offered().values_list("pk", flat=True))
        Member.objects.filter(pk=window.orienter_id).update(status=Member.Status.FORMER)

        assert window.pk not in set(OrientationAvailabilityBlock.objects.offered().values_list("pk", flat=True))
        with pytest.raises(OrientationError):
            orientations.request_block_orientation(
                window, MemberFactory(), window.starts_at, orientation_type=orientation_type
            )
        assert not OrientationSlot.objects.filter(block=window).exists()

    def it_stops_offering_a_window_whose_orientor_left_the_guild():
        window = OrientationAvailabilityBlockFactory()
        GuildStaffMembership.objects.filter(guild=window.guild, member=window.orienter).delete()
        assert window.pk not in set(OrientationAvailabilityBlock.objects.offered().values_list("pk", flat=True))

    def it_offers_the_leads_own_window():
        lead = MemberFactory()
        window = OrientationAvailabilityBlockFactory(guild=_guild(lead), orienter=lead)
        assert window.pk in set(OrientationAvailabilityBlock.objects.offered().values_list("pk", flat=True))


def describe_generation():
    def it_skips_a_lapsed_orientors_guild_rule():
        orientor = MemberFactory(status=Member.Status.SUSPENDED)
        guild = _guild(orientor)
        OrientationAvailabilityFactory(guild=guild, orienter=orientor)
        assert orientations.generate_slots(guild=guild) == 0

    def it_skips_a_lapsed_orientors_tool_rule_and_reads_each_owner_once():
        manager = MemberFactory(status=Member.Status.FORMER)
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        tool_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, guild=None)
        for weekday in (OrientationAvailability.Weekday.MONDAY, OrientationAvailability.Weekday.FRIDAY):
            OrientationAvailabilityFactory(
                equipment_owned=True, orientation_type=tool_type, orienter=manager, weekday=weekday
            )
        assert orientations.generate_slots(equipment=equipment) == 0


def describe_hidden_times_notice():
    """``Guild.hidden_times_notice``: the one line an admin reads after a lead change (#1129)."""

    LINE = "Gone Lead's orientation times are hidden. Add them as staff today to keep them."

    def _gone_lead() -> Member:
        return MemberFactory(full_legal_name="Gone Lead")

    def it_names_a_replaced_lead_with_a_recurring_row_here():
        gone = _gone_lead()
        guild = _guild(gone)
        OrientationAvailabilityFactory(guild=guild, orienter=gone)
        new = MemberFactory()
        guild.assign_lead(new)
        assert guild.hidden_times_notice(gone, new) == LINE

    def it_names_a_cleared_lead_with_an_upcoming_posted_time_here():
        gone = _gone_lead()
        guild = _guild(gone)
        _manual_slot(guild, gone)
        guild.clear_lead()
        assert guild.hidden_times_notice(gone, None) == LINE

    def it_names_a_lead_with_an_upcoming_open_window_here():
        gone = _gone_lead()
        guild = _guild(gone)
        OrientationAvailabilityBlockFactory(guild=guild, orienter=gone, orienter_on_staff=False)
        guild.clear_lead()
        assert guild.hidden_times_notice(gone, None) == LINE

    def it_says_nothing_for_a_lead_with_no_times_here():
        gone = _gone_lead()
        guild = _guild(gone)
        elsewhere = _guild(gone)
        OrientationAvailabilityFactory(guild=elsewhere, orienter=gone)
        past = _manual_slot(guild, gone)
        OrientationSlot.objects.filter(pk=past.pk).update(
            starts_at=timezone.now() - timedelta(days=2), ends_at=timezone.now() - timedelta(days=2, hours=-1)
        )
        cancelled = _manual_slot(guild, gone)
        cancelled.mark_cancelled()
        window = OrientationAvailabilityBlockFactory(guild=guild, orienter=gone, orienter_on_staff=False)
        window.cancel()
        guild.clear_lead()
        assert guild.hidden_times_notice(gone, None) == ""

    def it_says_nothing_when_they_stay_on_staff_or_nothing_changed():
        gone = _gone_lead()
        guild = _guild(gone)
        OrientationAvailabilityFactory(guild=guild, orienter=gone)
        assert guild.hidden_times_notice(None, gone) == ""
        assert guild.hidden_times_notice(gone, gone) == ""
        _staff(guild, gone)
        guild.assign_lead(MemberFactory())
        assert guild.hidden_times_notice(gone, guild.guild_lead) == ""
