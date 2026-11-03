"""BDD specs for equipment reserved by season (#1095): the model, the engine and the service.

With both season dates set an item is reserved by season: one reservation from local midnight on
the start day to local midnight after the end day, first come first served. The member's blockers,
the per member limit and approval still apply; duration, grid, opening hours and the booking
horizon do not. A season reservation cancels by its member until the season ends, and the card
line reads the season with its dates. Names are factory strings no changelog could contain
(STANDARDS.md section 8).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import icalendar
import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.utils import timezone

from membership import equipment as equipment_service
from membership.models import (
    Equipment,
    EquipmentError,
    EquipmentReservation,
    Member,
    season_dates_label,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

WAITING = EquipmentReservation.Status.PENDING_APPROVAL


def _member(username: str, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name or f"{username.title()} Furrowmere"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _bed(start_offset: int = 5, end_offset: int = 200, **kwargs) -> Equipment:
    """A garden bed whose season runs from ``start_offset`` to ``end_offset`` days from today."""
    today = timezone.localdate()
    return EquipmentFactory(
        name=kwargs.pop("name", "Quillowmere Bed 9Z"),
        kind=Equipment.Kind.SPACE,
        season_starts_on=today + timedelta(days=start_offset),
        season_ends_on=today + timedelta(days=end_offset),
        **kwargs,
    )


def _reserve(bed: Equipment, member: Member, **kwargs) -> EquipmentReservation:
    starts_at, _ = bed.season_span()
    return equipment_service.reserve(bed, member, starts_at, bed.season_minutes, **kwargs)


def _pacific(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def describe_season_span():
    def it_runs_from_pacific_midnight_to_midnight_after_the_last_day_across_a_dst_change():
        # Mar 1 2027 is still standard time (UTC-8); Jul 1 2027 is daylight time (UTC-7).
        bed = EquipmentFactory(season_starts_on=date(2027, 3, 1), season_ends_on=date(2027, 6, 30))
        starts_at, ends_at = bed.season_span()
        assert starts_at.astimezone(UTC) == datetime(2027, 3, 1, 8, 0, tzinfo=UTC)
        assert ends_at.astimezone(UTC) == datetime(2027, 7, 1, 7, 0, tzinfo=UTC)
        # 122 calendar days, less the hour the clocks went forward on Mar 14.
        assert bed.season_minutes == 122 * 24 * 60 - 60

    def it_ends_at_standard_time_midnight_after_daylight_time_ends():
        bed = EquipmentFactory(season_starts_on=date(2027, 4, 1), season_ends_on=date(2027, 11, 15))
        starts_at, ends_at = bed.season_span()
        assert starts_at.astimezone(UTC) == datetime(2027, 4, 1, 7, 0, tzinfo=UTC)
        assert ends_at.astimezone(UTC) == datetime(2027, 11, 16, 8, 0, tzinfo=UTC)

    def it_raises_without_both_dates():
        bed = EquipmentFactory(season_starts_on=date(2027, 4, 1))
        assert not bed.has_season
        assert bed.season_label == ""
        with pytest.raises(ValueError, match="has no season dates"):
            bed.season_span()


def describe_season_dates_label():
    def it_leaves_the_year_off_inside_this_year():
        year = timezone.localdate().year
        assert season_dates_label(date(year, 3, 1), date(year, 12, 15)) == "Mar 1 to Dec 15"
        assert season_dates_label(date(year, 3, 1), date(year, 12, 15), long=True) == "March 1 to December 15"

    def it_names_the_year_once_for_another_year():
        year = timezone.localdate().year + 1
        assert season_dates_label(date(year, 3, 1), date(year, 12, 15)) == f"Mar 1 to Dec 15, {year}"

    def it_names_both_years_across_a_new_year():
        assert season_dates_label(date(2027, 11, 1), date(2028, 2, 1)) == "Nov 1, 2027 to Feb 1, 2028"


def describe_reserving_a_season():
    def it_makes_one_confirmed_season_row_spanning_the_whole_season():
        bed = _bed()
        member = _member("sowerly")
        reservation = _reserve(bed, member)
        assert (reservation.starts_at, reservation.ends_at) == bed.season_span()
        assert reservation.is_season
        assert reservation.status == EquipmentReservation.Status.CONFIRMED
        assert EquipmentReservation.objects.filter(equipment=bed).count() == 1

    def it_skips_the_duration_horizon_grid_and_opening_hours():
        # Max 240 minutes, 30 days ahead, and no opening hours at all: a time slot could never be made.
        bed = _bed(start_offset=45, end_offset=300, max_duration_minutes=240, max_advance_days=30)
        assert not bed.hours_rules.exists()
        reservation = _reserve(bed, _member("tillwright"))
        assert reservation.duration_minutes > bed.max_duration_minutes
        assert timezone.localtime(reservation.starts_at).date() > timezone.localdate() + timedelta(days=30)

    def it_still_reserves_a_season_that_already_started_from_its_first_day():
        bed = _bed(start_offset=-20, end_offset=150)
        reservation = _reserve(bed, _member("latecomer"))
        assert reservation.starts_at == bed.season_span()[0]
        assert reservation.starts_at < timezone.now()

    def it_sends_the_usual_confirmation_naming_the_season():
        bed = _bed(start_offset=10, end_offset=100)
        _reserve(bed, _member("letterkin"))
        first, last = bed.season_starts_on, bed.season_ends_on
        when = f"The season, {season_dates_label(first, last, long=True)}"
        assert any(when in message.subject for message in mail.outbox)

    def it_refuses_a_second_member_first_come_first_served():
        bed = _bed()
        _reserve(bed, _member("earlybird"))
        with pytest.raises(EquipmentError, match=Equipment.SEASON_TAKEN_MESSAGE):
            _reserve(bed, _member("secondsower"))

    def it_refuses_a_span_that_is_not_the_season():
        bed = _bed()
        starts_at, _ = bed.season_span()
        with pytest.raises(EquipmentError, match=Equipment.SEASON_CHANGED_MESSAGE):
            equipment_service.reserve(bed, _member("stalepage"), starts_at, bed.season_minutes - 60)

    def it_refuses_once_the_season_ended():
        bed = _bed(start_offset=-100, end_offset=-1)
        with pytest.raises(EquipmentError, match=Equipment.SEASON_OVER_MESSAGE):
            _reserve(bed, _member("toolate"))

    def it_refuses_a_member_at_their_per_member_limit():
        bed = _bed(max_active_reservations_per_member=1)
        member = _member("capholder")
        # A time slot booked before the dates were set still counts toward the limit.
        EquipmentReservationFactory(equipment=bed, member=member)
        with pytest.raises(EquipmentError, match="You already have 1 upcoming reservation here"):
            _reserve(bed, member)

    def it_refuses_a_member_without_the_orientation():
        bed = _bed(unlocking_orientations=[OrientationTypeFactory(name="Soilwise Basics")])
        with pytest.raises(EquipmentError, match="Soilwise Basics"):
            _reserve(bed, _member("unoriented"))

    def it_refuses_a_member_who_has_not_agreed_to_the_usage_agreement():
        bed = _bed(usage_agreement_text="Water your bed weekly, Furrowmere rules.")
        with pytest.raises(EquipmentError, match="Agree to the usage agreement"):
            _reserve(bed, _member("unagreed"))

    def it_refuses_a_season_that_somehow_became_hourly():
        bed = _bed(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=500)
        with pytest.raises(EquipmentError, match="This season can't be reserved right now"):
            bed.ensure_reservable(_member("hourlyhopper"), bed.season_span()[0], bed.season_minutes)

    def it_refuses_while_a_booked_orientation_falls_inside_the_season():
        bed = _bed()
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=bed, name="Mulchcraft Walkthrough")
        start = _pacific(bed.season_starts_on + timedelta(days=3)) + timedelta(hours=10)
        slot = OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=start, ends_at=start + timedelta(hours=1)
        )
        OrientationBookingFactory(slot=slot)
        with pytest.raises(EquipmentError, match="A booked orientation falls inside this season"):
            _reserve(bed, _member("orientclash"))

    def it_ignores_a_past_time_slot_inside_a_season_that_already_started():
        bed = _bed(start_offset=-20, end_offset=150)
        past = timezone.now() - timedelta(days=3)
        EquipmentReservationFactory(equipment=bed, starts_at=past, ends_at=past + timedelta(hours=1))
        assert _reserve(bed, _member("pastisfine")).is_season

    def it_holds_the_season_as_a_request_when_approval_is_on_and_frees_it_on_decline():
        bed = _bed(requires_approval=True)
        manager = _member("bedwarden")
        EquipmentStaffMembershipFactory(equipment=bed, member=manager)
        request = _reserve(bed, _member("askfirst"))
        assert request.status == WAITING
        with pytest.raises(EquipmentError, match=Equipment.SEASON_TAKEN_MESSAGE):
            _reserve(bed, _member("waitbehind"))
        request.decline(manager, reason="Bed 9Z is resting this year.")
        assert _reserve(bed, _member("nextinline")).status == WAITING

    def it_holds_a_donation_season_awaiting_payment_through_checkout():
        bed = _bed(pricing=Equipment.Pricing.DONATION)
        session = {"id": "cs_season_1095", "url": "https://checkout.stripe.example/cs_season_1095"}
        with patch("billing.stripe_utils.create_checkout_session", return_value=session) as create:
            url = equipment_service.start_reservation_checkout(
                bed, _member("payseason"), bed.season_span()[0], bed.season_minutes, amount_cents=1500
            )
        assert url == session["url"]
        hold = EquipmentReservation.objects.get(equipment=bed)
        assert (hold.status, hold.is_season) == (EquipmentReservation.Status.PENDING_PAYMENT, True)
        assert create.call_args.kwargs["amount_cents"] == 1500
        assert "The season, " in create.call_args.kwargs["product_name"]


def describe_cancelling_a_season():
    def it_lets_the_member_cancel_after_the_season_started_and_frees_it():
        bed = _bed(start_offset=-20, end_offset=150)
        holder = _member("giveback")
        reservation = _reserve(bed, holder)
        assert reservation.can_self_cancel(timezone.now())
        reservation.cancel(holder)
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        assert _reserve(bed, _member("nextgardener")).is_season

    def it_refuses_once_the_season_is_over():
        holder = _member("overdue")
        past = timezone.now() - timedelta(days=200)
        reservation = EquipmentReservationFactory(
            equipment=_bed(), member=holder, starts_at=past, ends_at=past + timedelta(days=100), is_season=True
        )
        assert not reservation.can_self_cancel(timezone.now())
        with pytest.raises(EquipmentError, match="This reservation already ended."):
            reservation.cancel(holder)

    def it_still_refuses_a_started_time_slot():
        holder = _member("slotstarted")
        started = timezone.now() - timedelta(minutes=30)
        reservation = EquipmentReservationFactory(
            member=holder, starts_at=started, ends_at=started + timedelta(hours=2)
        )
        assert not reservation.can_self_cancel(timezone.now())
        with pytest.raises(EquipmentError, match="already started"):
            reservation.cancel(holder)


def describe_season_state_and_card_line():
    def it_reads_open_with_the_dates():
        bed = _bed()
        assert bed.season_state() == Equipment.SeasonState.OPEN
        assert bed.availability_line() == ("free", f"Open for the season, {bed.season_label}")

    def it_reads_reserved_while_a_request_or_booking_holds_it():
        bed = _bed(requires_approval=True)
        _reserve(bed, _member("heldbyrequest"))
        assert bed.availability_line() == ("busy", f"Reserved for the season, {bed.season_label}")

    def it_reads_the_prefetched_holds_without_a_query(django_assert_num_queries):
        bed = _bed()
        starts_at, ends_at = bed.season_span()
        bed.season_holds = [EquipmentReservation(starts_at=starts_at, ends_at=ends_at)]
        with django_assert_num_queries(0):
            assert bed.season_state() == Equipment.SeasonState.RESERVED
        bed.season_holds = []
        with django_assert_num_queries(0):
            assert bed.season_state() == Equipment.SeasonState.OPEN

    def it_reads_ended_once_the_last_day_is_over():
        bed = _bed(start_offset=-100, end_offset=-1)
        assert bed.availability_line() == ("muted", f"Season ended, {bed.season_label}")

    def it_still_reads_closed_first():
        bed = _bed(is_closed=True, closed_message="Frost on the Furrowmere beds.")
        assert bed.availability_line() == ("closed", "Closed. Frost on the Furrowmere beds.")

    def it_is_open_again_after_the_holder_cancels():
        bed = _bed()
        holder = _member("changedmind")
        _reserve(bed, holder).cancel(holder)
        assert bed.season_state() == Equipment.SeasonState.OPEN


def describe_season_bookings_inside():
    def it_counts_upcoming_time_slots_and_blocks_inside_the_dates_only():
        bed = EquipmentFactory()
        today = timezone.localdate()
        soon = _pacific(today + timedelta(days=3)) + timedelta(hours=10)
        EquipmentReservationFactory(equipment=bed, starts_at=soon, ends_at=soon + timedelta(hours=1))
        EquipmentReservationFactory(
            equipment=bed,
            kind=EquipmentReservation.Kind.BLOCK,
            starts_at=soon + timedelta(days=1),
            ends_at=soon + timedelta(days=1, hours=1),
        )
        # Outside the dates, already over, cancelled, or a season row: none of them count.
        EquipmentReservationFactory(
            equipment=bed, starts_at=soon + timedelta(days=40), ends_at=soon + timedelta(days=40, hours=1)
        )
        past = timezone.now() - timedelta(days=2)
        EquipmentReservationFactory(equipment=bed, starts_at=past, ends_at=past + timedelta(hours=1))
        EquipmentReservationFactory(
            equipment=bed,
            status=EquipmentReservation.Status.CANCELLED,
            starts_at=soon + timedelta(hours=2),
            ends_at=soon + timedelta(hours=3),
        )
        EquipmentReservationFactory(
            equipment=bed, is_season=True, starts_at=soon + timedelta(days=5), ends_at=soon + timedelta(days=9)
        )
        assert bed.season_bookings_inside(today - timedelta(days=10), today + timedelta(days=20)) == 2


def describe_season_reservation_words():
    def it_labels_its_days_and_what_it_asks_for():
        bed = _bed()
        reservation = _reserve(bed, _member("wordsmith"))
        assert reservation.season_days() == (bed.season_starts_on, bed.season_ends_on)
        assert reservation.season_label == bed.season_label
        assert reservation.request_span == f"the season, {bed.season_label}"
        assert equipment_service.when_display(reservation).startswith("The season, ")

    def it_keeps_the_time_slot_words_for_a_slot():
        starts_at = timezone.make_aware(datetime(2027, 9, 11, 14, 0))
        reservation = EquipmentReservationFactory(starts_at=starts_at, ends_at=starts_at + timedelta(hours=2))
        assert reservation.season_label == ""
        assert reservation.request_span == "Sat, Sep 11, 2:00 PM to 4:00 PM"

    def it_sends_an_all_day_invite_that_leaves_the_calendar_free():
        bed = _bed()
        reservation = _reserve(bed, _member("calendarer"))
        event = icalendar.Calendar.from_ical(
            equipment_service.build_ics(reservation, method="REQUEST", status="CONFIRMED")
        ).walk("VEVENT")[0]
        assert event.decoded("dtstart") == bed.season_starts_on
        assert event.decoded("dtend") == bed.season_ends_on + timedelta(days=1)
        assert str(event["transp"]) == "TRANSPARENT"

    def it_does_not_change_a_time_slot_invite():
        bed = EquipmentFactory()
        day = timezone.localdate() + timedelta(days=2)
        EquipmentHoursFactory(equipment=bed, weekday=day.weekday(), start_time=time(9, 0), end_time=time(17, 0))
        starts_at = timezone.make_aware(datetime.combine(day, time(10, 0)))
        reservation = equipment_service.reserve(bed, _member("slotinvite"), starts_at, 60)
        assert not reservation.is_season
        event = icalendar.Calendar.from_ical(
            equipment_service.build_ics(reservation, method="REQUEST", status="CONFIRMED")
        ).walk("VEVENT")[0]
        assert event.decoded("dtstart") == starts_at
        assert "transp" not in event
