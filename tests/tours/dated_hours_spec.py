"""Tour hours on picked dates (#1160): the model, what visitors are offered and booking them. Adding them is My Hours' + Add Dates since #1300 (``tests/hours/editing_spec.py``)."""

from __future__ import annotations

import threading
import time as clock
from datetime import UTC, date, time, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from hours.follow import lock_person
from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import (
    TourBookingFactory,
    TourCalendarConnectionFactory,
    TourDayOffFactory,
    TourHoursFactory,
    local,
)
from tours import booking as booking_service
from tours.availability import open_tour_at, open_tours
from tours.booking import TourFullError, Visitor, book_tour
from tours.models import FacilityTourSettings, TourBooking, TourDayOff, TourHours, day_label

pytestmark = pytest.mark.django_db

# 2030-01-07 is a Monday. Saturday the 12th has dated hours in the availability specs; Saturday the
# 19th, the same weekday a week on, has none.
NOW = local(date(2030, 1, 7), 9)
SATURDAY = date(2030, 1, 12)
NEXT_SATURDAY = date(2030, 1, 19)
ELEVEN = local(SATURDAY, 11)


def _ahead(weekday: int, weeks: int = 1) -> date:
    """The first ``weekday`` at least ``weeks`` weeks from today: clear of the notice, inside a year."""
    day = timezone.localdate() + timedelta(weeks=weeks)
    return day + timedelta(days=(weekday - day.weekday()) % 7)


def _dated(guide: Member, day: date, **kwargs: Any) -> TourHours:
    """Dated hours on ``day``: 11:00 AM to 3:00 PM, 60 minute tours, 6 seats unless given."""
    values: dict[str, Any] = {"start_time": time(11), "end_time": time(15), "seats": 6}
    values.update(kwargs)
    return TourHoursFactory(guide=guide, date=day, weekday=day.weekday(), **values)


def _visitor(party: int = 2, email: str = "sam@example.com") -> Visitor:
    return Visitor(name="Sam Rivera", email=email, phone="", party_size=party)


def describe_the_model():
    def it_holds_a_dated_rows_weekday_to_its_date(guide: Member):
        with pytest.raises(IntegrityError), transaction.atomic():
            TourHoursFactory(guide=guide, date=SATURDAY, weekday=TourHours.Weekday.SUNDAY)

    def it_stores_the_dates_weekday_and_keeps_weekly_rows_dateless(guide: Member):
        dated = _dated(guide, SATURDAY)
        weekly = TourHoursFactory(guide=guide)
        assert (dated.weekday, dated.date, weekly.date) == (TourHours.Weekday.SATURDAY, SATURDAY, None)
        assert list(TourHours.objects.weekly()) == [weekly]
        assert list(TourHours.objects.dated()) == [dated]

    def it_applies_a_dated_row_on_its_date_only(guide: Member):
        dated = _dated(guide, SATURDAY)
        weekly = TourHoursFactory(guide=guide, weekday=TourHours.Weekday.SATURDAY)
        assert (dated.applies_on(SATURDAY), dated.applies_on(NEXT_SATURDAY)) == (True, False)
        assert (weekly.applies_on(SATURDAY), weekly.applies_on(NEXT_SATURDAY)) == (True, True)
        assert weekly.applies_on(SATURDAY + timedelta(days=1)) is False

    def it_clashes_with_its_weekday_and_its_date_never_another_date(guide: Member):
        dated = _dated(guide, SATURDAY)
        weekly = TourHours(weekday=TourHours.Weekday.SATURDAY, start_time=time(14), end_time=time(16))
        same_day = TourHours(date=SATURDAY, weekday=5, start_time=time(14), end_time=time(16))
        next_week = TourHours(date=NEXT_SATURDAY, weekday=5, start_time=time(11), end_time=time(15))
        friday = TourHours(weekday=TourHours.Weekday.FRIDAY, start_time=time(11), end_time=time(15))
        later = TourHours(date=SATURDAY, weekday=5, start_time=time(15), end_time=time(16))
        assert [dated.overlaps(other) for other in (weekly, same_day, next_week, friday, later)] == [
            True,
            True,
            False,
            False,
            False,
        ]

    def it_reads_one_line_per_dated_window(guide: Member):
        assert _dated(guide, date(2026, 11, 15)).dated_summary == (
            "Sun, Nov 15 · 11:00 AM to 3:00 PM · 1 hour each · 6 seats"
        )
        one = _dated(guide, date(2026, 11, 14), start_time=time(12, 30), end_time=time(13, 30), seats=1)
        assert one.dated_summary == "Sat, Nov 14 · 12:30 PM to 1:30 PM · 1 hour each · 1 seat"
        odd = _dated(guide, date(2026, 11, 13), tour_minutes=75)
        assert odd.dated_summary == "Fri, Nov 13 · 11:00 AM to 3:00 PM · 75 minutes each · 6 seats"
        assert str(one) == "Gina Guide: Sat 2026-11-14 12:30 to 13:30"

    def it_refuses_a_dated_summary_for_weekly_hours(guide: Member):
        with pytest.raises(ValueError, match="Only dated"):
            _ = TourHoursFactory(guide=guide).dated_summary

    def it_lists_upcoming_dated_hours_soonest_first_and_keeps_the_past(guide: Member):
        today = timezone.localdate()
        later = _dated(guide, today + timedelta(days=9))
        past = _dated(guide, today - timedelta(days=2))
        soon = _dated(guide, today)
        assert list(TourHours.objects.upcoming_dated()) == [soon, later]
        assert TourHours.objects.filter(pk=past.pk).exists()

    def it_names_every_day_a_day_off_covers_inside_a_range(guide: Member):
        TourDayOffFactory(guide=guide, starts_on=date(2030, 1, 10), ends_on=date(2030, 1, 12))
        TourDayOffFactory(guide=guide, starts_on=date(2030, 1, 20), ends_on=date(2030, 1, 25))
        assert TourDayOff.objects.covered_days(date(2030, 1, 11), date(2030, 1, 21)) == [
            date(2030, 1, 11),
            date(2030, 1, 12),
            date(2030, 1, 20),
            date(2030, 1, 21),
        ]

    def it_labels_a_day_short(guide: Member):
        assert day_label(date(2026, 11, 15)) == "Sun, Nov 15"


def describe_what_visitors_are_offered():
    def it_offers_a_dated_window_on_its_date_and_not_a_week_later(guide: Member):
        _dated(guide, SATURDAY)
        tours = open_tours(SATURDAY, NEXT_SATURDAY, now=NOW)
        assert [timezone.localtime(tour.starts_at).strftime("%Y-%m-%d %H:%M") for tour in tours] == [
            "2030-01-12 11:00",
            "2030-01-12 12:00",
            "2030-01-12 13:00",
            "2030-01-12 14:00",
        ]
        assert {tour.seats for tour in tours} == {6}

    def it_offers_dated_and_weekly_hours_side_by_side(guide: Member):
        _dated(guide, SATURDAY, start_time=time(11), end_time=time(12))
        TourHoursFactory(guide=guide, weekday=TourHours.Weekday.SATURDAY, start_time=time(15), end_time=time(16))
        starts = [timezone.localtime(tour.starts_at).hour for tour in open_tours(SATURDAY, NEXT_SATURDAY, now=NOW)]
        assert starts == [11, 15, 15]

    def it_offers_nothing_on_a_day_off(guide: Member):
        _dated(guide, SATURDAY)
        TourDayOffFactory(guide=guide, starts_on=SATURDAY, ends_on=SATURDAY)
        assert open_tours(SATURDAY, SATURDAY, now=NOW) == []
        assert open_tour_at(guide, ELEVEN, now=NOW) is None

    def it_follows_the_horizon_and_the_notice(guide: Member):
        _dated(guide, SATURDAY)
        FacilityTourSettings.objects.filter(pk=FacilityTourSettings.load().pk).update(booking_horizon_days=3)
        assert open_tours(SATURDAY, SATURDAY, now=NOW) == []
        FacilityTourSettings.objects.update(booking_horizon_days=30, min_notice_hours=24)
        late = local(date(2030, 1, 11), 12)
        assert [timezone.localtime(tour.starts_at).hour for tour in open_tours(SATURDAY, SATURDAY, now=late)] == [
            12,
            13,
            14,
        ]

    def it_counts_seats_and_drops_a_full_tour(guide: Member):
        _dated(guide, SATURDAY, seats=2)
        TourBookingFactory(guide=guide, starts_at=ELEVEN, party_size=2)
        assert ELEVEN.astimezone(UTC) not in [tour.starts_at for tour in open_tours(SATURDAY, SATURDAY, now=NOW)]
        full = open_tour_at(guide, ELEVEN, now=NOW)
        assert full is not None and full.is_full

    def it_hides_a_busy_time_from_visitors_but_not_from_staff(guide: Member, google_on: None):
        _dated(guide, SATURDAY, end_time=time(12))
        start = ELEVEN.astimezone(UTC)
        TourCalendarConnectionFactory(
            guide=guide,
            busy_spans=[[start.isoformat(), (start + timedelta(minutes=15)).isoformat()]],
            busy_checked_at=timezone.now(),
        )
        assert open_tours(SATURDAY, SATURDAY, now=NOW) == []
        assert open_tour_at(guide, ELEVEN, now=NOW) is None
        assert [tour.starts_at for tour in open_tours(SATURDAY, SATURDAY, now=NOW, staff=True)] == [start]
        assert open_tour_at(guide, ELEVEN, now=NOW, staff=True) is not None

    def it_finds_one_dated_tour_and_not_the_same_hour_a_week_on(guide: Member):
        row = _dated(guide, SATURDAY)
        tour = open_tour_at(guide, ELEVEN, now=NOW)
        assert tour is not None and (tour.hours_id, tour.seats, tour.seats_left) == (row.pk, 6, 6)
        assert open_tour_at(guide, local(NEXT_SATURDAY, 11), now=NOW) is None

    def it_reads_only_the_dated_rows_in_range_in_the_same_seven_queries(guide: Member, django_assert_num_queries):
        _dated(guide, SATURDAY)
        _dated(guide, NEXT_SATURDAY + timedelta(days=7))
        FacilityTourSettings.load()
        with django_assert_num_queries(7):
            tours = open_tours(SATURDAY, NEXT_SATURDAY, now=NOW)
        assert len(tours) == 4


def describe_booking_a_dated_tour():
    def it_books_a_seat_and_refuses_one_past_the_last(guide: Member):
        _dated(guide, SATURDAY, seats=3)
        booking = book_tour(guide=guide, starts_at=ELEVEN, visitor=_visitor(2), now=NOW)
        assert booking.starts_at == ELEVEN
        with pytest.raises(TourFullError):
            book_tour(guide=guide, starts_at=ELEVEN, visitor=_visitor(2, email="other@example.com"), now=NOW)
        assert TourBooking.objects.seats_taken(guide, ELEVEN) == 2

    def it_finds_the_dated_tour_under_the_lock(guide: Member):
        _dated(guide, SATURDAY)
        with transaction.atomic():
            tour = open_tour_at(guide, ELEVEN, lock=True, now=NOW)
        assert tour is not None and tour.seats_left == 6

    @pytest.mark.postgres_only
    def it_locks_the_dated_row_for_update(guide: Member):
        row = _dated(guide, SATURDAY)
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            tour = open_tour_at(guide, ELEVEN, lock=True, now=NOW)
        assert tour is not None and tour.hours_id == row.pk
        locked = [query["sql"] for query in queries if "FOR UPDATE" in query["sql"]]
        assert len(locked) == 1 and '"tours_facilitytoursettings"' in locked[0]

    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_lets_one_of_four_racing_visitors_take_the_last_seats():
        guide = make_guide(full_legal_name="Gina Guide")
        _dated(guide, SATURDAY, seats=2)
        # Made up front, as the weekly race does: a first load() inside each racing transaction
        # would serialize the visitors on the settings row, hiding whether the hours lock works.
        FacilityTourSettings.load()
        real_open_tour_at = booking_service.tour_at
        start = threading.Barrier(4)
        outcomes: list[str] = []

        def slow_open_tour_at(*args: Any, **kwargs: Any) -> Any:
            tour = real_open_tour_at(*args, **kwargs)
            clock.sleep(0.3)
            return tour

        def visitor(number: int) -> None:
            try:
                start.wait()
                book_tour(guide=guide, starts_at=ELEVEN, visitor=_visitor(2, email=f"v{number}@example.com"), now=NOW)
                outcomes.append("booked")
            except TourFullError:
                outcomes.append("filled")
            finally:
                connection.close()

        with patch.object(booking_service, "tour_at", slow_open_tour_at):
            threads = [threading.Thread(target=visitor, args=(number,)) for number in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert sorted(outcomes) == ["booked", "filled", "filled", "filled"]
        assert TourBooking.objects.seats_taken(guide, ELEVEN) == 2


def describe_the_surfaces_that_offer_tours():
    def it_shows_a_dated_tour_on_the_visitors_booking_page(guide: Member):
        day = _ahead(5)
        _dated(guide, day)
        content = Client(HTTP_HOST="tours.pastlives.space").get("/", {"date": day.isoformat()}).content.decode()
        assert f"{guide.pk}-{local(day, 11).astimezone(UTC):%Y%m%dT%H%MZ}" in content

    def it_shows_a_dated_tour_at_the_kiosk(guide: Member):
        day = _ahead(5)
        _dated(guide, day)
        client = Client(HTTP_HOST="tours.pastlives.space")
        client.get(reverse("tours_kiosk:home"), {"key": FacilityTourSettings.load().rotate_kiosk_key()})
        content = client.get(reverse("tours_kiosk:book"), {"date": day.isoformat()}).content.decode()
        assert f"{guide.pk}-{local(day, 11).astimezone(UTC):%Y%m%dT%H%MZ}" in content

    def it_offers_a_dated_tour_in_the_staff_reschedule(guide: Member):
        day = _ahead(5)
        _dated(guide, day)
        booking = TourBookingFactory(guide=guide, starts_at=local(day, 12), party_size=1)
        response = signed_in(guide).get(
            reverse("tours:booking_reschedule", args=[booking.pk]),
            {"date": day.isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert f"{guide.pk}-{local(day, 13).astimezone(UTC):%Y%m%dT%H%MZ}" in response.content.decode()


def describe_saving_under_the_guide_lock():
    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_lets_a_booking_through_while_a_save_holds_the_guide():
        # A booking's insert takes FOR KEY SHARE on its guide's member row; a plain FOR UPDATE
        # there would block it, and with the hours rows locked the other way round, deadlock.
        guide = make_guide()
        held = threading.Event()
        release = threading.Event()

        def hold() -> None:
            try:
                with transaction.atomic():
                    lock_person(guide.pk)
                    held.set()
                    release.wait(5)
            finally:
                connection.close()

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            assert held.wait(5)
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '2s'")
                TourBookingFactory(guide=guide)
        finally:
            release.set()
            holder.join()
        assert TourBooking.objects.filter(guide=guide).count() == 1
