"""Tour hours on picked dates (#1160): the Available On calendar on My Hours, and what visitors are offered."""

from __future__ import annotations

import json
import re
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
from tours.forms import TourDatesForm
from tours.models import FacilityTourSettings, TourBooking, TourDayOff, TourHours, day_label, lock_guide_hours

pytestmark = pytest.mark.django_db

HOURS = reverse("tours:hours")
SAVE = reverse("tours:hours_save")
DATES_SAVE = reverse("tours:dates_save")

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


def _post(guide: Member, *days: date, **values: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "guide": str(guide.pk),
        "dates": [day.isoformat() for day in days],
        "start_time": "11:00",
        "end_time": "15:00",
        "tour_minutes": "60",
        "seats": "6",
    }
    data.update(values)
    return data


def _weekly_post(guide: Member, **values: str) -> dict[str, str]:
    """One new Every Week row: Tuesday 4:00 to 6:00 PM, 60 minute tours, 4 seats unless given."""
    row = {"weekday": "1", "start_time": "16:00", "end_time": "18:00", "tour_minutes": "60", "seats": "4", **values}
    return {
        "guide": str(guide.pk),
        "hours-TOTAL_FORMS": "1",
        "hours-INITIAL_FORMS": "0",
        "hours-MIN_NUM_FORMS": "0",
        "hours-MAX_NUM_FORMS": "1000",
        **{f"hours-0-{name}": value for name, value in row.items()},
    }


def _errors(content: str) -> list[str]:
    return re.findall(r"data-tour-dates-error>([^<]*)</p>", content)


def _calendar(content: str) -> dict[str, Any]:
    match = re.search(r'<script id="tour-dates-config" type="application/json">(.*?)</script>', content)
    assert match is not None
    return json.loads(match.group(1))


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

    def it_reads_only_the_dated_rows_in_range_in_the_same_four_queries(guide: Member, django_assert_num_queries):
        _dated(guide, SATURDAY)
        _dated(guide, NEXT_SATURDAY + timedelta(days=7))
        FacilityTourSettings.load()
        with django_assert_num_queries(4):
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
        assert len(locked) == 1 and '"tours_tourhours"."weekday" = 5' in locked[0]

    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_lets_one_of_four_racing_visitors_take_the_last_seats():
        guide = make_guide(full_legal_name="Gina Guide")
        _dated(guide, SATURDAY, seats=2)
        # Made up front, as the weekly race does: a first load() inside each racing transaction
        # would serialize the visitors on the settings row, hiding whether the hours lock works.
        FacilityTourSettings.load()
        real_open_tour_at = open_tour_at
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

        with patch.object(booking_service, "open_tour_at", slow_open_tour_at):
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


def describe_adding_dates():
    def it_adds_hours_on_every_picked_date_in_one_save(guide: Member):
        days = [_ahead(5), _ahead(6), _ahead(5, weeks=2)]
        response = signed_in(guide).post(DATES_SAVE, _post(guide, *days), follow=True)
        rows = list(TourHours.objects.dated().order_by("date"))
        assert [(row.date, row.weekday, row.start_time, row.end_time, row.tour_minutes, row.seats) for row in rows] == [
            (day, day.weekday(), time(11), time(15), 60, 6) for day in sorted(days)
        ]
        assert response.redirect_chain == [(HOURS, 302)]
        assert "Added 3 dates." in response.content.decode()

    def it_says_one_date_for_one(guide: Member):
        response = signed_in(guide).post(DATES_SAVE, _post(guide, _ahead(2)), follow=True)
        assert "Added 1 date." in response.content.decode()

    def it_counts_a_date_picked_twice_once(guide: Member):
        day = _ahead(2)
        data = _post(guide, day)
        data["dates"] = [day.isoformat(), day.isoformat(), "not-a-date"]
        signed_in(guide).post(DATES_SAVE, data)
        assert TourHours.objects.dated().count() == 1

    def it_asks_for_a_date(guide: Member):
        response = signed_in(guide).post(DATES_SAVE, _post(guide))
        assert response.status_code == 200
        assert _errors(response.content.decode()) == ["Pick at least one date."]

    def it_refuses_every_date_that_clashes_and_keeps_nothing(guide: Member):
        saturday, sunday, monday = _ahead(5), _ahead(6), _ahead(0, weeks=2)
        TourHoursFactory(guide=guide, weekday=5, start_time=time(11), end_time=time(16))
        _dated(guide, sunday, start_time=time(13), end_time=time(15))
        TourDayOffFactory(guide=guide, starts_on=monday, ends_on=monday)
        fine = _ahead(2)
        response = signed_in(guide).post(DATES_SAVE, _post(guide, saturday, sunday, monday, fine))
        assert response.status_code == 200
        assert _errors(response.content.decode()) == [
            f"{day_label(saturday)}: overlaps your weekly Saturday hours, 11:00 AM to 4:00 PM.",
            f"{day_label(sunday)}: overlaps hours already on that date, 1:00 PM to 3:00 PM.",
            f"{day_label(monday)} is a day off. Delete the day off first.",
        ]
        assert TourHours.objects.dated().count() == 1

    def it_lets_back_to_back_hours_on_one_date_through(guide: Member):
        day = _ahead(3)
        _dated(guide, day, start_time=time(9), end_time=time(11))
        signed_in(guide).post(DATES_SAVE, _post(guide, day))
        assert TourHours.objects.filter(date=day).count() == 2

    def it_refuses_a_past_date_and_one_more_than_a_year_ahead(guide: Member):
        today = timezone.localdate()
        past, far = today - timedelta(days=1), today + timedelta(days=366)
        response = signed_in(guide).post(DATES_SAVE, _post(guide, past, far))
        assert _errors(response.content.decode()) == [
            f"{day_label(past)} has passed. Pick today or a day ahead.",
            f"{day_label(far)} is more than a year ahead.",
        ]
        assert not TourHours.objects.exists()

    def it_wants_to_after_from_and_room_for_a_tour(guide: Member):
        client = signed_in(guide)
        backwards = client.post(DATES_SAVE, _post(guide, _ahead(2), start_time="15:00", end_time="11:00"))
        assert "End time must be after the start time." in backwards.content.decode()
        short = client.post(DATES_SAVE, _post(guide, _ahead(2), start_time="11:00", end_time="11:30"))
        assert "These hours are shorter than one tour." in short.content.decode()
        assert not TourHours.objects.exists()

    def it_reopens_the_panel_with_the_picks_and_fields_kept(guide: Member):
        TourDayOffFactory(guide=guide, starts_on=_ahead(5), ends_on=_ahead(5))
        days = [_ahead(5), _ahead(6)]
        content = signed_in(guide).post(DATES_SAVE, _post(guide, *days, seats="9")).content.decode()
        assert "plTourDates('tour-dates-config', true)" in content
        assert _calendar(content)["picked"] == [day.isoformat() for day in days]
        assert 'name="seats" value="9"' in content
        assert "data-tour-dates-refused" in content

    def it_starts_the_panel_closed_with_tour_settings_defaults(guide: Member):
        FacilityTourSettings.load()
        FacilityTourSettings.objects.update(default_tour_minutes=75, default_seats=8)
        content = signed_in(guide).get(HOURS).content.decode()
        assert "plTourDates('tour-dates-config', false)" in content
        assert "data-tour-dates-hint>Tap each day you are free. Tap it again to unpick.</p>" in content
        assert 'x-show="!open" @click="open = true"' in content
        assert 'name="seats" value="8"' in content
        assert '<option value="75" selected>75</option>' in content
        assert 'data-guidance-target="tours.add-dates"' in content

    def it_marks_the_days_with_hours_and_the_days_off(guide: Member):
        today = timezone.localdate()
        _dated(guide, today + timedelta(days=3))
        _dated(guide, today - timedelta(days=3))
        TourDayOffFactory(guide=guide, starts_on=today + timedelta(days=4), ends_on=today + timedelta(days=5))
        calendar = _calendar(signed_in(guide).get(HOURS).content.decode())
        assert calendar == {
            "today": today.isoformat(),
            "last": (today + timedelta(days=365)).isoformat(),
            "dated": [(today + timedelta(days=3)).isoformat()],
            "off": [(today + timedelta(days=4)).isoformat(), (today + timedelta(days=5)).isoformat()],
            "picked": [],
        }

    def it_adds_a_tour_length_tour_settings_names_off_the_list(guide: Member):
        FacilityTourSettings.load()
        FacilityTourSettings.objects.update(default_tour_minutes=75)
        form = TourDatesForm(guide=guide, defaults=FacilityTourSettings.load())
        assert ("75", "75") in form.fields["tour_minutes"].choices  # type: ignore[attr-defined]
        assert form.picked == [] and form.picked_iso == []


def describe_the_available_on_list():
    def it_shows_the_empty_state(guide: Member):
        content = signed_in(guide).get(HOURS).content.decode()
        assert (
            "data-tour-dates-empty>No dates added yet. Add the days you are free, a month at a time if you like.</p>"
            in content
        )

    def it_lists_upcoming_dated_hours_soonest_first(guide: Member):
        today = timezone.localdate()
        later = _dated(guide, today + timedelta(days=9), seats=1)
        soon = _dated(guide, today + timedelta(days=2))
        _dated(guide, today - timedelta(days=1))
        content = signed_in(guide).get(HOURS).content.decode()
        assert re.findall(r'data-tour-dated="(\d+)"', content) == [str(soon.pk), str(later.pk)]
        assert soon.dated_summary in content
        assert "Delete these hours?" in content
        assert "Visitors can no longer book these times. Tours already booked stay booked." in content

    def it_deletes_one_window_and_says_so(guide: Member):
        row = _dated(guide, _ahead(2))
        other = _dated(guide, _ahead(3))
        response = signed_in(guide).post(reverse("tours:dates_delete", args=[row.pk]), follow=True)
        assert response.redirect_chain == [(HOURS, 302)]
        assert "Hours deleted." in response.content.decode()
        assert list(TourHours.objects.all()) == [other]

    def it_never_deletes_weekly_hours_through_it(guide: Member):
        weekly = TourHoursFactory(guide=guide)
        assert signed_in(guide).post(reverse("tours:dates_delete", args=[weekly.pk])).status_code == 404
        assert TourHours.objects.filter(pk=weekly.pk).exists()

    def it_refuses_a_guide_deleting_another_guides_dates(guide: Member):
        row = _dated(make_guide(), _ahead(2))
        assert signed_in(guide).post(reverse("tours:dates_delete", args=[row.pk])).status_code == 403
        assert TourHours.objects.filter(pk=row.pk).exists()

    def it_refuses_a_get(guide: Member):
        row = _dated(guide, _ahead(2))
        assert signed_in(guide).get(reverse("tours:dates_delete", args=[row.pk])).status_code == 405


def describe_the_every_week_section():
    def it_says_it_repeats_and_points_irregular_schedules_at_available_on(guide: Member):
        content = signed_in(guide).get(HOURS).content.decode()
        assert (
            "Hours that repeat every week. If your schedule changes from week to week, leave this empty and use "
            "Available On." in content
        )
        assert content.index(">Every Week</h2>") < content.index(">Available On</h2>") < content.index(">Days Off</h2>")
        assert content.index(">Days Off</h2>") < content.index("data-tour-gcal")

    def it_never_shows_dated_rows(guide: Member):
        weekly = TourHoursFactory(guide=guide)
        dated = _dated(guide, _ahead(2))
        content = signed_in(guide).get(HOURS).content.decode()
        assert 'name="hours-INITIAL_FORMS" value="1"' in content
        assert f'name="hours-0-id" value="{weekly.pk}"' in content
        assert f'value="{dated.pk}" id="id_hours' not in content

    def it_saves_the_weekly_rows_and_leaves_dated_rows_alone(guide: Member):
        dated = _dated(guide, _ahead(5))
        data = {
            "guide": str(guide.pk),
            "hours-TOTAL_FORMS": "1",
            "hours-INITIAL_FORMS": "0",
            "hours-MIN_NUM_FORMS": "0",
            "hours-MAX_NUM_FORMS": "1000",
            "hours-0-weekday": "1",
            "hours-0-start_time": "16:00",
            "hours-0-end_time": "18:00",
            "hours-0-tour_minutes": "60",
            "hours-0-seats": "4",
        }
        assert signed_in(guide).post(SAVE, data).status_code == 302
        assert TourHours.objects.weekly().count() == 1
        assert TourHours.objects.filter(pk=dated.pk).exists()

    def it_refuses_weekly_hours_over_upcoming_dated_hours_naming_the_soonest_date(guide: Member):
        later, soon = _ahead(5, weeks=2), _ahead(5)
        _dated(guide, later)
        _dated(guide, soon, start_time=time(12), end_time=time(14))
        _dated(guide, soon - timedelta(weeks=2), start_time=time(9), end_time=time(17))  # passed: never named
        data = _weekly_post(guide, weekday="5", start_time="11:00", end_time="16:00")
        response = signed_in(guide).post(SAVE, data)
        assert response.status_code == 200
        assert (
            f"Every Saturday 11:00 AM to 4:00 PM overlaps your hours on {day_label(soon)}, 12:00 PM to 2:00 PM."
            in response.content.decode()
        )
        assert not TourHours.objects.weekly().exists()

    def it_lets_weekly_hours_beside_dated_hours_that_day_through(guide: Member):
        _dated(guide, _ahead(5))
        assert (
            signed_in(guide)
            .post(SAVE, _weekly_post(guide, weekday="5", start_time="15:00", end_time="17:00"))
            .status_code
            == 302
        )
        assert TourHours.objects.weekly().count() == 1

    def it_ignores_past_dated_hours(guide: Member):
        past = timezone.localdate() - timedelta(days=3)
        _dated(guide, past)
        data = _weekly_post(guide, weekday=str(past.weekday()), start_time="11:00", end_time="15:00")
        assert signed_in(guide).post(SAVE, data).status_code == 302


def describe_an_admin_for_another_guide():
    def it_adds_and_deletes_dates_for_them(admin: Member, guide: Member):
        client = signed_in(admin)
        content = client.get(HOURS, {"guide": guide.pk}).content.decode()
        assert "data-tour-dates-panel" in content
        response = client.post(DATES_SAVE, _post(guide, _ahead(2)))
        assert response["Location"] == f"{HOURS}?guide={guide.pk}"
        row = TourHours.objects.get()
        assert row.guide == guide
        response = client.post(reverse("tours:dates_delete", args=[row.pk]))
        assert response["Location"] == f"{HOURS}?guide={guide.pk}"
        assert not TourHours.objects.exists()

    def it_refuses_a_guide_adding_dates_for_another(guide: Member):
        other = make_guide()
        assert signed_in(guide).post(DATES_SAVE, _post(other, _ahead(2))).status_code == 403
        assert not TourHours.objects.exists()


def describe_saving_under_the_guide_lock():
    @pytest.mark.django_db(transaction=True)
    def it_refuses_to_lock_outside_a_transaction():
        with pytest.raises(RuntimeError, match="transaction.atomic"):
            lock_guide_hours(make_guide())

    @pytest.mark.postgres_only
    def it_locks_the_guide_before_checking_and_adding_dates(guide: Member):
        client = signed_in(guide)
        with CaptureQueriesContext(connection) as queries:
            assert client.post(DATES_SAVE, _post(guide, _ahead(2))).status_code == 302
        sql = [query["sql"] for query in queries.captured_queries]
        lock = next(index for index, text in enumerate(sql) if "FOR UPDATE" in text)
        assert '"membership_member"' in sql[lock] and str(guide.pk) in sql[lock]
        insert = next(index for index, text in enumerate(sql) if text.startswith('INSERT INTO "tours_tourhours"'))
        check = next(index for index, text in enumerate(sql) if '"tours_tourdayoff"' in text)
        assert lock < check < insert

    @pytest.mark.postgres_only
    def it_locks_the_guide_before_checking_and_saving_weekly_hours(guide: Member):
        client = signed_in(guide)
        with CaptureQueriesContext(connection) as queries:
            assert client.post(SAVE, _weekly_post(guide)).status_code == 302
        sql = [query["sql"] for query in queries.captured_queries]
        lock = next(index for index, text in enumerate(sql) if "FOR UPDATE" in text)
        assert '"membership_member"' in sql[lock]
        insert = next(index for index, text in enumerate(sql) if text.startswith('INSERT INTO "tours_tourhours"'))
        assert lock < insert

    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_keeps_one_of_two_clashing_saves_onto_an_empty_date(admin: Member):
        guide = make_guide(full_legal_name="Gina Guide")
        FacilityTourSettings.load()
        day = _ahead(5)
        clients = [signed_in(guide), signed_in(admin)]
        posts = [
            _post(guide, day, start_time="11:00", end_time="15:00", seats="6"),
            _post(guide, day, start_time="13:00", end_time="17:00", seats="10"),
        ]
        real_save = TourDatesForm.save
        start = threading.Barrier(2)
        codes: list[int] = []

        def slow_save(form: TourDatesForm) -> list[TourHours]:
            # Widen the gap between the overlap check and the write: without the lock both saves
            # would pass the check here and both windows would land.
            clock.sleep(0.3)
            return real_save(form)

        def save(number: int) -> None:
            try:
                start.wait()
                codes.append(clients[number].post(DATES_SAVE, posts[number]).status_code)
            finally:
                connection.close()

        with patch.object(TourDatesForm, "save", slow_save):
            threads = [threading.Thread(target=save, args=(number,)) for number in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert sorted(codes) == [200, 302]
        assert TourHours.objects.filter(guide=guide, date=day).count() == 1
