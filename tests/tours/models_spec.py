"""Facility tour models (#947): the settings singleton, tour hours, days off and bookings."""

from __future__ import annotations

from datetime import UTC, date, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from membership.models import Member
from tests.membership.factories import MemberFactory
from tests.tours.conftest import make_guide
from tests.tours.factories import TourBookingFactory, TourDayOffFactory, TourHoursFactory, local
from tours.models import FacilityTourSettings, TourBooking, TourDayOff, TourHours, guide_filter

pytestmark = pytest.mark.django_db


def describe_facility_tour_settings():
    def it_loads_one_row_with_the_issue_defaults():
        tour_settings = FacilityTourSettings.load()
        assert (
            tour_settings.pk,
            tour_settings.accepting_bookings,
            tour_settings.booking_horizon_days,
            tour_settings.min_notice_hours,
            tour_settings.cancel_cutoff_hours,
            tour_settings.default_tour_minutes,
            tour_settings.default_seats,
        ) == (1, True, 30, 24, 24, 60, 6)
        assert str(tour_settings) == "Tour Settings"

    def it_stays_one_row_however_it_is_saved():
        FacilityTourSettings.load()
        FacilityTourSettings(booking_horizon_days=7).save()
        assert FacilityTourSettings.objects.count() == 1
        assert FacilityTourSettings.load().booking_horizon_days == 7


def describe_tour_hours():
    def it_names_the_guide_day_and_times():
        hours = TourHoursFactory(guide=MemberFactory(full_legal_name="Gina Guide"))
        assert str(hours) == "Gina Guide: Tuesday 16:00 to 18:00"

    def it_refuses_times_off_the_half_hour_grid():
        hours = TourHoursFactory.build(guide=MemberFactory(), start_time=time(16, 15), end_time=time(18, 0, 30))
        with pytest.raises(ValidationError) as caught:
            hours.full_clean()
        assert set(caught.value.message_dict) == {"start_time", "end_time"}

    def it_refuses_an_end_before_the_start():
        hours = TourHoursFactory.build(guide=MemberFactory(), start_time=time(18, 0), end_time=time(16, 0))
        with pytest.raises(ValidationError) as caught:
            hours.full_clean()
        assert caught.value.message_dict["end_time"] == ["End time must be after the start time."]

    def it_refuses_hours_shorter_than_one_tour():
        hours = TourHoursFactory.build(guide=MemberFactory(), end_time=time(16, 30), tour_minutes=60)
        with pytest.raises(ValidationError) as caught:
            hours.full_clean()
        assert caught.value.message_dict["tour_minutes"] == ["These hours are shorter than one tour."]

    def it_leaves_a_missing_time_to_the_field_error():
        hours = TourHoursFactory.build(guide=MemberFactory(), start_time=None)
        with pytest.raises(ValidationError) as caught:
            hours.full_clean()
        assert set(caught.value.message_dict) == {"start_time"}

    def it_accepts_a_window_one_tour_long():
        TourHoursFactory.build(guide=MemberFactory(), end_time=time(17, 0)).full_clean()

    def it_refuses_more_than_fifty_seats():
        hours = TourHoursFactory.build(guide=MemberFactory(), seats=51)
        with pytest.raises(ValidationError) as caught:
            hours.full_clean()
        assert caught.value.message_dict["seats"] == ["Use 1 to 50 seats."]

    def it_overlaps_only_on_the_same_weekday_and_shared_minutes():
        hours = TourHoursFactory.build()
        assert hours.overlaps(TourHoursFactory.build(start_time=time(17, 30), end_time=time(19, 0)))
        assert not hours.overlaps(TourHoursFactory.build(start_time=time(18, 0), end_time=time(19, 0)))
        assert not hours.overlaps(TourHoursFactory.build(weekday=TourHours.Weekday.WEDNESDAY))

    def it_carves_back_to_back_tours_on_a_day():
        hours = TourHoursFactory.build(end_time=time(18, 0), tour_minutes=45)
        tuesday = date(2030, 1, 8)
        # 4:00 to 6:00 holds two 45 minute tours; a third would run to 6:15.
        assert hours.carve_spans(tuesday) == [
            (local(tuesday, 16), local(tuesday, 16, 45)),
            (local(tuesday, 16, 45), local(tuesday, 17, 30)),
        ]

    def it_drops_the_hour_the_clocks_skip_in_spring():
        # 2027-03-14: 2:00 AM does not exist in Portland; 1:00 PST and 3:00 PDT are an hour apart.
        hours = TourHoursFactory.build(weekday=TourHours.Weekday.SUNDAY, start_time=time(1, 0), end_time=time(4, 0))
        spans = hours.carve_spans(date(2027, 3, 14))
        assert [
            (start.astimezone(UTC).strftime("%H:%M"), end.astimezone(UTC) - start.astimezone(UTC))
            for start, end in spans
        ] == [
            ("09:00", timedelta(hours=1)),
            ("10:00", timedelta(hours=1)),
        ]
        assert [f"{start:%H:%M}" for start, _end in spans] == ["01:00", "03:00"]

    def it_keeps_every_tour_one_real_hour_when_the_clocks_fall_back():
        # 2026-11-01: 1:00 AM happens twice; wall 1:00 to 2:00 is two real hours.
        hours = TourHoursFactory.build(weekday=TourHours.Weekday.SUNDAY, start_time=time(1, 0), end_time=time(3, 0))
        spans = hours.carve_spans(date(2026, 11, 1))
        assert [
            (start.astimezone(UTC).strftime("%H:%M"), end.astimezone(UTC) - start.astimezone(UTC))
            for start, end in spans
        ] == [
            ("08:00", timedelta(hours=1)),
            ("10:00", timedelta(hours=1)),
        ]

    def it_drops_a_tour_whose_real_end_runs_past_the_window():
        # 2027-03-14, 0:30 to 2:30 AM: the 1:30 PST tour really ends at 3:30 PDT, past 2:30 on the wall.
        hours = TourHoursFactory.build(weekday=TourHours.Weekday.SUNDAY, start_time=time(0, 30), end_time=time(2, 30))
        spans = hours.carve_spans(date(2027, 3, 14))
        assert [(f"{start:%H:%M}", f"{end:%H:%M}") for start, end in spans] == [("00:30", "01:30")]

    def it_offers_only_guides_hours():
        guide = make_guide()
        TourHoursFactory(guide=guide)
        TourHoursFactory(guide=MemberFactory())
        assert [hours.guide for hours in TourHours.objects.offered()] == [guide]
        assert list(TourHours.objects.for_guide(guide)) == list(TourHours.objects.filter(guide=guide))


def describe_guide_filter():
    def it_admits_active_tour_guides_admins_and_superusers():
        guide = make_guide()
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        MemberFactory()
        make_guide(status=Member.Status.FORMER)
        MemberFactory(fog_role=Member.FogRole.ADMIN, status=Member.Status.FORMER)
        assert set(Member.objects.filter(guide_filter()).distinct()) == {guide, admin}


def describe_tour_day_off():
    def it_labels_one_day_and_a_run_of_days():
        one = TourDayOffFactory.build(starts_on=date(2030, 12, 23), ends_on=date(2030, 12, 23))
        run = TourDayOffFactory.build(starts_on=date(2030, 12, 22), ends_on=date(2031, 1, 2))
        assert one.label == "Mon, Dec 23"
        assert run.label == "Sun, Dec 22 to Thu, Jan 2"
        assert str(
            TourDayOffFactory(guide=MemberFactory(full_legal_name="Gina Guide"), starts_on=date(2030, 12, 23))
        ) == ("Gina Guide off Mon, Dec 23")

    def it_covers_every_day_of_its_run():
        run = TourDayOffFactory.build(starts_on=date(2030, 1, 1), ends_on=date(2030, 1, 3))
        assert [run.covers(date(2030, 1, day)) for day in range(0 + 1, 5)] == [True, True, True, False]

    def it_lists_upcoming_runs_including_one_under_way():
        today = timezone.localdate()
        guide = MemberFactory()
        TourDayOffFactory(guide=guide, starts_on=today - timedelta(days=5), ends_on=today - timedelta(days=1))
        under_way = TourDayOffFactory(
            guide=guide, starts_on=today - timedelta(days=1), ends_on=today + timedelta(days=1)
        )
        later = TourDayOffFactory(guide=guide, starts_on=today + timedelta(days=10))
        assert list(TourDayOff.objects.upcoming()) == [under_way, later]

    def it_finds_the_runs_touching_a_range():
        run = TourDayOffFactory(starts_on=date(2030, 1, 5), ends_on=date(2030, 1, 9))
        assert list(TourDayOff.objects.touching(date(2030, 1, 9), date(2030, 1, 20))) == [run]
        assert list(TourDayOff.objects.touching(date(2030, 1, 10), date(2030, 1, 20))) == []


def describe_tour_booking():
    def it_gets_its_own_random_self_serve_token():
        first, second = TourBookingFactory(), TourBookingFactory()
        assert len(first.self_serve_token) >= 40
        assert first.self_serve_token != second.self_serve_token

    def it_holds_seats_only_while_booked_or_attended():
        assert [TourBookingFactory.build(status=status).holds_seats for status in TourBooking.Status.values] == [
            True,
            False,
            True,
            False,
        ]

    def it_names_the_visitor_party_and_local_start():
        booking = TourBookingFactory(visitor_name="Vera Visitor", party_size=3, starts_at=local(date(2030, 1, 8), 16))
        assert str(booking) == "Vera Visitor (3) 2030-01-08 16:00"

    def it_sums_the_seats_one_tour_holds():
        guide = MemberFactory()
        start = local(date(2030, 1, 8), 16)
        TourBookingFactory(guide=guide, starts_at=start, party_size=2)
        TourBookingFactory(guide=guide, starts_at=start, party_size=1, status=TourBooking.Status.ATTENDED)
        TourBookingFactory(guide=guide, starts_at=start, party_size=5, status=TourBooking.Status.CANCELLED)
        assert TourBooking.objects.seats_taken(guide, start) == 3
        assert TourBooking.objects.seats_taken(guide, start + timedelta(hours=1)) == 0
