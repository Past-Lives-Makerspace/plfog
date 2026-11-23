"""Guide hours may overlap; one tour runs at a time, and the first booking claims it (#1309, #1312).

Felix (#1312): more people should become tour guides, and first posted ownership left new guides
slim pickings. So hours may overlap; visitors see each time once with no guide named; the first
booking at a time claims it for one guide, later bookings at that start join that tour and every
other guide's tour overlapping it, even partly, stops being offered that day. A time nobody has
booked goes to the guide with the fewest upcoming booked tours, the hours posted first breaking a
tie. The booking page's calendar marks days with a booked tour that has room, and that day's booked
tours come first, saying how many people are going. Confirm First is gone (#1309) and a request
still waiting is booked.
"""

from __future__ import annotations

import re
import threading
import time as clock
from datetime import UTC, date, time, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourDayOffFactory, TourHoursFactory, local
from tours import booking as booking_service
from tours.availability import open_tour_at, open_tours, tour_at, upcoming_tours_by_guide
from tours.booking import (
    TourFullError,
    TourUnavailableError,
    Visitor,
    answer_tour_proposal,
    book_tour,
    cancel_tour_by_visitor,
    propose_tour_time,
    reschedule_tour,
)
from tours.models import FacilityTourSettings, TourBooking, TourGuideSettings, TourHours, notice_label
from tours.notify import guide_notice
from tours.picker import build_picker

pytestmark = pytest.mark.django_db

# 2030-01-07 is a Monday; tours run Tuesday the 8th, 4:00 and 5:00 PM.
NOW = local(date(2030, 1, 7), 9)
TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)
FIVE = local(TUESDAY, 17)
SAVE = reverse("tours:hours_save")
DATES = reverse("tours:dates_save")
AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}
HOST = "tours.pastlives.space"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    cache.clear()


@pytest.fixture
def amber() -> Member:
    """The guide who posted first: Tuesday 4:00 to 6:00 PM, every week."""
    member = make_guide(full_legal_name="Amber Capwell")
    TourHoursFactory(guide=member)
    return member


@pytest.fixture
def dayzie() -> Member:
    return make_guide(full_legal_name="Dayzie Dee")


@pytest.fixture
def both(amber: Member, dayzie: Member) -> tuple[Member, Member]:
    """Dayzie posted the very same Tuesday hours after Amber."""
    TourHoursFactory(guide=dayzie)
    return amber, dayzie


def _hours_post(guide: Member, *rows: dict[str, str], initial: int = 0) -> dict[str, str]:
    data = {
        "guide": str(guide.pk),
        "hours-TOTAL_FORMS": str(len(rows)),
        "hours-INITIAL_FORMS": str(initial),
        "hours-MIN_NUM_FORMS": "0",
        "hours-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        values = {"weekday": "1", "start_time": "16:00", "end_time": "18:00", "tour_minutes": "60", "seats": "4"}
        values.update(row)
        data.update({f"hours-{index}-{name}": value for name, value in values.items()})
    return data


def _next(weekday: int) -> date:
    """The next local day with ``weekday`` after today."""
    today = timezone.localdate()
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


def _visitor(party: int = 2, email: str = "sam@example.com") -> Visitor:
    return Visitor(name="Sam Rivera", email=email, phone="", party_size=party)


def _starts(tours: list[Any]) -> list[tuple[str, str]]:
    return [(tour.guide.greeting_name.split()[0], f"{timezone.localtime(tour.starts_at):%H:%M}") for tour in tours]


def describe_posting_hours_another_guide_has():
    def it_saves_weekly_hours_over_another_guides(amber: Member, dayzie: Member):
        data = _hours_post(dayzie, {"start_time": "16:30", "end_time": "18:30"})
        assert signed_in(dayzie).post(SAVE, data, **AUTOSAVE).status_code == 200
        assert TourHours.objects.filter(guide=dayzie).count() == 1

    def it_saves_a_picked_date_inside_another_guides_weekly_hours(amber: Member, dayzie: Member):
        tuesday = _next(1)
        data = {
            "guide": str(dayzie.pk),
            "dates": [tuesday.isoformat()],
            "start_time": "16:00",
            "end_time": "18:00",
            "tour_minutes": "60",
            "seats": "4",
        }
        signed_in(dayzie).post(DATES, data)
        assert TourHours.objects.filter(guide=dayzie, date=tuesday).exists()

    def it_still_refuses_a_guides_own_rows_overlapping(dayzie: Member):
        data = _hours_post(dayzie, {}, {"start_time": "17:00", "end_time": "19:00"})
        assert signed_in(dayzie).post(SAVE, data, **AUTOSAVE).status_code == 422

    @pytest.mark.postgres_only
    def it_locks_only_the_guides_own_row_so_bookings_never_wait_on_a_save(dayzie: Member):
        with CaptureQueriesContext(connection) as queries:
            signed_in(dayzie).post(SAVE, _hours_post(dayzie, {}), **AUTOSAVE)
        locks = [query["sql"] for query in queries.captured_queries if " FOR " in query["sql"]]
        assert len(locks) == 1 and '"membership_member"' in locks[0]


def describe_one_tour_at_a_time():
    def it_shows_visitors_each_time_once_and_staff_every_guides(both: tuple[Member, Member]):
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW)) == [("Amber", "16:00"), ("Amber", "17:00")]
        assert len(open_tours(TUESDAY, TUESDAY, now=NOW, staff=True)) == 4

    def it_joins_every_later_booking_to_the_tour_the_first_one_claimed(both: tuple[Member, Member]):
        amber, dayzie = both
        TourBookingFactory(guide=amber, starts_at=local(TUESDAY + timedelta(days=7), 16))  # Dayzie has fewer
        first = book_tour(guide=dayzie, starts_at=FOUR, visitor=_visitor(1), now=NOW)
        # A page rendered before the claim still names Amber: the booking joins Dayzie's tour.
        second = book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(2, "kim@example.com"), now=NOW)
        assert (first.guide, second.guide) == (dayzie, dayzie)
        (four,) = [tour for tour in open_tours(TUESDAY, TUESDAY, now=NOW) if tour.starts_at == FOUR]
        assert (four.guide, four.seats_left) == (dayzie, 1)

    def it_stops_offering_another_guides_tour_that_overlaps_it_even_partly(amber: Member, dayzie: Member):
        TourHoursFactory(guide=dayzie, start_time=time(16, 30), end_time=time(18, 30))
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW)) == [
            ("Amber", "16:00"),
            ("Dayzie", "16:30"),
            ("Amber", "17:00"),
            ("Dayzie", "17:30"),
        ]
        book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW)
        # 4:30 to 5:30 runs into Amber's 4:00 tour; 5:30 does not.
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW)) == [
            ("Amber", "16:00"),
            ("Amber", "17:00"),
            ("Dayzie", "17:30"),
        ]
        assert ("Dayzie", "16:30") not in _starts(open_tours(TUESDAY, TUESDAY, now=NOW, staff=True))
        assert open_tour_at(dayzie, local(TUESDAY, 16, 30), now=NOW, staff=True) is None
        with pytest.raises(TourUnavailableError):
            book_tour(guide=dayzie, starts_at=local(TUESDAY, 16, 30), visitor=_visitor(1, "kim@example.com"), now=NOW)

    def it_blocks_that_day_only(amber: Member, dayzie: Member):
        TourHoursFactory(guide=dayzie, start_time=time(16, 30), end_time=time(18, 30))
        book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW)
        next_week = TUESDAY + timedelta(days=7)
        assert ("Dayzie", "16:30") in _starts(open_tours(next_week, next_week, now=NOW))

    def it_shows_a_full_claimed_time_as_full_and_opens_no_second_tour(both: tuple[Member, Member]):
        amber, dayzie = both
        book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(4), now=NOW)
        assert FOUR not in [tour.starts_at for tour in open_tours(TUESDAY, TUESDAY, now=NOW)]
        (four,) = [tour for tour in open_tours(TUESDAY, TUESDAY, include_full=True, now=NOW) if tour.starts_at == FOUR]
        assert (four.guide, four.is_full) == (amber, True)
        with pytest.raises(TourFullError):
            book_tour(guide=dayzie, starts_at=FOUR, visitor=_visitor(1, "kim@example.com"), now=NOW)

    def it_still_holds_the_time_when_the_claimed_guide_is_off_that_day(both: tuple[Member, Member]):
        amber, _dayzie = both
        book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW)
        TourDayOffFactory(guide=amber, starts_on=TUESDAY, ends_on=TUESDAY)
        assert FOUR not in [tour.starts_at for tour in open_tours(TUESDAY, TUESDAY, now=NOW)]

    def it_frees_the_time_once_the_booking_is_cancelled(both: tuple[Member, Member]):
        amber, dayzie = both
        TourBookingFactory(guide=amber, starts_at=local(TUESDAY + timedelta(days=7), 16))
        booked = book_tour(guide=dayzie, starts_at=FOUR, visitor=_visitor(), now=NOW)
        cancel_tour_by_visitor(booked, now=NOW)
        assert ("Dayzie", "16:00") in _starts(open_tours(TUESDAY, TUESDAY, now=NOW))

    def it_keeps_two_tours_booked_at_one_time_before_this_change(both: tuple[Member, Member]):
        amber, dayzie = both
        TourBookingFactory(guide=dayzie, starts_at=FOUR, party_size=1)
        TourBookingFactory(guide=amber, starts_at=FOUR, party_size=1)
        # Visitors see the time once, the hours posted first; staff keep both tours.
        assert [tour.guide for tour in open_tours(TUESDAY, TUESDAY, now=NOW) if tour.starts_at == FOUR] == [amber]
        assert {tour.guide for tour in open_tours(TUESDAY, TUESDAY, now=NOW, staff=True) if tour.starts_at == FOUR} == {
            amber,
            dayzie,
        }


def describe_who_gets_a_time_nobody_has_booked():
    def it_goes_to_the_guide_with_the_fewest_upcoming_tours(both: tuple[Member, Member]):
        amber, dayzie = both
        TourBookingFactory(guide=amber, starts_at=local(TUESDAY + timedelta(days=7), 16))
        tour = tour_at(FOUR, now=NOW)
        assert tour is not None and tour.guide == dayzie
        assert book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW).guide == dayzie

    def it_breaks_a_tie_with_the_hours_posted_first(both: tuple[Member, Member]):
        amber, _dayzie = both
        tour = tour_at(FOUR, now=NOW)
        assert tour is not None and tour.guide == amber

    def it_counts_tours_not_parties_nor_past_or_cancelled_ones(both: tuple[Member, Member]):
        amber, dayzie = both
        later = local(TUESDAY + timedelta(days=7), 16)
        TourBookingFactory.create_batch(2, guide=amber, starts_at=later)  # two parties, one tour
        TourBookingFactory(guide=dayzie, starts_at=later + timedelta(hours=1))
        TourBookingFactory(guide=dayzie, starts_at=NOW - timedelta(days=2))
        TourBookingFactory(guide=dayzie, starts_at=later, status=TourBooking.Status.CANCELLED)
        assert upcoming_tours_by_guide(NOW) == {amber.pk: 1, dayzie.pk: 1}
        tour = tour_at(FOUR, now=NOW)
        assert tour is not None and tour.guide == amber

    def it_leaves_out_the_booking_staff_are_moving(both: tuple[Member, Member]):
        amber, _dayzie = both
        moving = TourBookingFactory(guide=amber, starts_at=local(TUESDAY + timedelta(days=7), 16))
        assert upcoming_tours_by_guide(NOW) == {amber.pk: 1}
        assert upcoming_tours_by_guide(NOW, moving=moving) == {}

    def it_counts_only_guides_who_can_give_it(both: tuple[Member, Member]):
        amber, dayzie = both
        TourBookingFactory(guide=amber, starts_at=local(TUESDAY + timedelta(days=7), 16))
        TourDayOffFactory(guide=dayzie, starts_on=TUESDAY, ends_on=TUESDAY)
        tour = tour_at(FOUR, now=NOW)
        assert tour is not None and tour.guide == amber

    def it_asks_one_more_query_only_when_two_guides_could_give_a_time(
        both: tuple[Member, Member], django_assert_num_queries: Any
    ):
        FacilityTourSettings.load()
        # Settings, hours, days off, bookings, Confirm First notice, then the guides' upcoming tours.
        with django_assert_num_queries(6):
            open_tours(TUESDAY, TUESDAY, now=NOW)

    def it_is_who_the_kiosk_names(both: tuple[Member, Member]):
        amber, dayzie = both
        TourBookingFactory(guide=amber, starts_at=local(TUESDAY + timedelta(days=7), 16))
        times = build_picker(open_tours(TUESDAY, TUESDAY, now=NOW), "", name_guides=True).times
        assert [choice.guide_name for choice in times] == ["Dayzie", "Dayzie"]


def _race(bookings: list[tuple[Member, Any, str]]) -> list[str]:
    """Book each ``(guide, start, email)`` at once from its own thread; what each one came to."""
    real_tour_at = booking_service.tour_at
    start = threading.Barrier(len(bookings))
    outcomes: list[str] = []

    def slow_tour_at(*args: Any, **kwargs: Any) -> Any:
        # Widen the gap between reading who holds a time and saving the booking: without the lock
        # every visitor would read the day with nothing booked yet.
        tour = real_tour_at(*args, **kwargs)
        clock.sleep(0.3)
        return tour

    def visitor(member: Member, starts_at: Any, email: str) -> None:
        try:
            start.wait()
            outcomes.append(
                book_tour(guide=member, starts_at=starts_at, visitor=_visitor(1, email), now=NOW).guide.greeting_name
            )
        except TourUnavailableError:
            outcomes.append("gone")
        finally:
            connection.close()

    with patch.object(booking_service, "tour_at", slow_tour_at):
        threads = [threading.Thread(target=visitor, args=booking) for booking in bookings]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    return sorted(outcomes)


def describe_two_visitors_booking_at_once():
    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_ends_two_visitors_on_one_time_on_one_guide():
        amber = make_guide(full_legal_name="Amber Capwell")
        dayzie = make_guide(full_legal_name="Dayzie Dee")
        for member in (amber, dayzie):
            TourHoursFactory(guide=member)
        FacilityTourSettings.load()
        outcomes = _race([(amber, FOUR, "a@example.com"), (dayzie, FOUR, "d@example.com")])
        assert outcomes == ["Amber Capwell", "Amber Capwell"]
        assert TourBooking.objects.values("guide_id").distinct().count() == 1

    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_never_lets_two_overlapping_tours_both_book():
        amber = make_guide(full_legal_name="Amber Capwell")
        dayzie = make_guide(full_legal_name="Dayzie Dee")
        TourHoursFactory(guide=amber)
        TourHoursFactory(guide=dayzie, start_time=time(16, 30), end_time=time(18, 30))
        # Made up front: a first load() inside each racing transaction would insert the row and
        # serialize the visitors on its primary key, hiding whether the lock works.
        FacilityTourSettings.load()
        outcomes = _race([(amber, FOUR, "a@example.com"), (dayzie, local(TUESDAY, 16, 30), "d@example.com")])
        assert len(outcomes) == 2 and "gone" in outcomes
        assert TourBooking.objects.count() == 1

    @pytest.mark.postgres_only
    def it_locks_the_tour_settings_row_before_reading_who_holds_the_time(both: tuple[Member, Member]):
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            tour_at(FOUR, lock=True, now=NOW)
        locked = [query["sql"] for query in queries.captured_queries if "FOR UPDATE" in query["sql"]]
        assert len(locked) == 1 and '"tours_facilitytoursettings"' in locked[0]

    @pytest.mark.django_db(transaction=True)
    def it_refuses_to_lock_outside_a_transaction():
        with pytest.raises(RuntimeError, match="transaction.atomic"):
            tour_at(FOUR, lock=True, now=NOW)


def describe_the_booking_page():
    @pytest.fixture
    def day(both: tuple[Member, Member]) -> date:
        """A day four days out with both guides' 4:00 to 6:00 PM, so the live pages' clock admits it."""
        day = timezone.localdate() + timedelta(days=4)
        day += timedelta(days=1) if day.weekday() == TUESDAY.weekday() else timedelta(0)  # not over their Tuesdays
        for member in both:
            TourHoursFactory(guide=member, weekday=day.weekday())
        return day

    def it_marks_a_day_with_a_booked_tour_that_has_room(both: tuple[Member, Member], day: date):
        amber, _dayzie = both
        TourBookingFactory(guide=amber, starts_at=local(day, 17), party_size=3, visitor_name="Pat Private")
        page = Client(HTTP_HOST=HOST).get(f"/?date={day:%Y-%m-%d}").content.decode()
        assert f'data-tour-day="{day:%Y-%m-%d}" data-tour-going-day' in page
        assert page.count("data-tour-going-day") == 1 and "data-tour-going-key" in page
        assert f'aria-label="{day:%A, %B} {day.day}, people going"' in page
        assert "Pat Private" not in page

    def it_lists_the_booked_tour_first_saying_how_many_are_going(both: tuple[Member, Member], day: date):
        amber, _dayzie = both
        TourBookingFactory(guide=amber, starts_at=local(day, 17), party_size=3)
        page = Client(HTTP_HOST=HOST).get(f"/?date={day:%Y-%m-%d}").content.decode()
        first, second = page.split('data-tour-time="')[1:3]
        assert "5:00 PM" in first and "3 people going, join them" in first and "pl-tourpub__time--going" in page
        assert "4:00 PM" in second and "going" not in second

    def it_marks_nothing_when_no_tour_is_booked_or_the_booked_one_is_full(both: tuple[Member, Member], day: date):
        amber, _dayzie = both
        page = Client(HTTP_HOST=HOST).get(f"/?date={day:%Y-%m-%d}").content.decode()
        assert "data-tour-going" not in page
        TourBookingFactory(guide=amber, starts_at=local(day, 17), party_size=4)
        page = Client(HTTP_HOST=HOST).get(f"/?date={day:%Y-%m-%d}").content.decode()
        assert "data-tour-going" not in page

    def it_books_a_shared_time_and_the_next_visitor_joins_the_same_guide(both: tuple[Member, Member], day: date):
        amber, dayzie = both
        key = f"{amber.pk}-{local(day, 16).astimezone(UTC):%Y%m%dT%H%MZ}"
        data = {"tour": key, "name": "Sam Rivera", "phone": "503 555 0142", "party_size": "2"}
        client = Client(HTTP_HOST=HOST)
        assert client.post("/", {**data, "email": "sam@example.com"}).status_code == 302
        page = client.get(f"/?date={day:%Y-%m-%d}").content.decode()
        assert "2 people going, join them" in page
        dayzie_key = f"{dayzie.pk}-{local(day, 16).astimezone(UTC):%Y%m%dT%H%MZ}"
        assert client.post("/", {**data, "tour": dayzie_key, "email": "kim@example.com"}).status_code == 302
        assert set(TourBooking.objects.values_list("guide_id", flat=True)) == {amber.pk}

    def it_orders_booked_tours_first_only_when_asked(both: tuple[Member, Member]):
        amber, _dayzie = both
        TourBookingFactory(guide=amber, starts_at=FIVE, party_size=1)
        tours = open_tours(TUESDAY, TUESDAY, now=NOW)
        assert [choice.going for choice in build_picker(tours, "", join_first=True).times] == [1, 0]
        assert [choice.going for choice in build_picker(tours, "").times] == [0, 1]


def describe_moving_a_tour():
    @pytest.fixture
    def wednesday_tour(amber: Member, dayzie: Member) -> TourBooking:
        """A visitor on Dayzie's Wednesday 4:00 PM tour, who may move to Tuesday."""
        TourHoursFactory(guide=dayzie, weekday=2)
        return book_tour(guide=dayzie, starts_at=FOUR + timedelta(days=1), visitor=_visitor(), now=NOW)

    def it_moves_a_visitor_into_a_claimed_time_onto_that_tour(
        amber: Member, dayzie: Member, wednesday_tour: TourBooking
    ):
        TourHoursFactory(guide=dayzie)
        book_tour(guide=amber, starts_at=FIVE, visitor=_visitor(1, "kim@example.com"), now=NOW)
        moved = reschedule_tour(wednesday_tour, guide=dayzie, starts_at=FIVE, now=NOW)
        assert (moved.guide, moved.starts_at) == (amber, FIVE)

    def it_refuses_a_visitor_moving_onto_a_tour_another_booking_blocks(
        amber: Member, dayzie: Member, wednesday_tour: TourBooking
    ):
        TourHoursFactory(guide=dayzie, start_time=time(16, 30), end_time=time(18, 30))
        book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(1, "kim@example.com"), now=NOW)
        with pytest.raises(TourUnavailableError):
            reschedule_tour(wednesday_tour, guide=dayzie, starts_at=local(TUESDAY, 16, 30), now=NOW)

    def it_lets_a_visitor_move_off_their_own_tour_onto_one_it_overlapped(amber: Member, dayzie: Member):
        TourHoursFactory(guide=dayzie, start_time=time(16, 30), end_time=time(18, 30))
        booked = book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW)
        # Their own 4:00 booking is the only thing blocking Dayzie's 4:30; it never blocks their own move.
        moved = reschedule_tour(booked, guide=dayzie, starts_at=local(TUESDAY, 16, 30), now=NOW)
        assert (moved.guide, moved.starts_at) == (dayzie, local(TUESDAY, 16, 30))

    def it_never_hands_a_visitor_their_own_start_with_another_guide(both: tuple[Member, Member]):
        amber, _dayzie = both
        booked = book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW)
        with pytest.raises(TourUnavailableError, match="already your tour time"):
            reschedule_tour(booked, guide=amber, starts_at=FOUR, now=NOW)

    def it_refuses_staff_asking_to_move_onto_a_blocked_tour(amber: Member, dayzie: Member, wednesday_tour: TourBooking):
        TourHoursFactory(guide=dayzie, start_time=time(16, 30), end_time=time(18, 30))
        book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(1, "kim@example.com"), now=NOW)
        signed_in(dayzie)
        dayzie.refresh_from_db()
        with pytest.raises(TourUnavailableError):
            propose_tour_time(
                wednesday_tour, guide=dayzie, starts_at=local(TUESDAY, 16, 30), actor=dayzie.user, now=NOW
            )
        staff = open_tours(TUESDAY, TUESDAY, guide=dayzie, now=NOW, staff=True, moving=wednesday_tour)
        assert local(TUESDAY, 16, 30) not in [tour.starts_at for tour in staff]

    def it_lets_staff_hand_a_tour_to_another_guide_at_the_same_time(both: tuple[Member, Member]):
        amber, dayzie = both
        booked = book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW)
        signed_in(amber)
        amber.refresh_from_db()
        staff = open_tours(TUESDAY, TUESDAY, guide=dayzie, now=NOW, staff=True, moving=booked)
        assert FOUR in [tour.starts_at for tour in staff]
        proposed = propose_tour_time(booked, guide=dayzie, starts_at=FOUR, actor=amber.user, now=NOW)
        assert answer_tour_proposal(proposed, accept=True, proposal=proposed.proposal_key, now=NOW) == "accepted"
        booked.refresh_from_db()
        assert (booked.guide, booked.starts_at) == (dayzie, FOUR)


TWENTY_TO_FOUR = local(TUESDAY, 15, 40)
NOTICE = reverse("tours:notice_save")


def _notice(member: Member, minutes: int | None) -> None:
    TourGuideSettings.objects.update_or_create(guide=member, defaults={"min_notice_minutes": minutes})


def describe_each_guides_own_notice():
    """Felix (#1312): every guide sets their own minimum notice; Tour Settings' 24 hours is a default, never a floor."""

    def it_offers_a_15_minute_guide_20_minutes_out_and_not_a_guide_on_the_default(both: tuple[Member, Member]):
        amber, dayzie = both
        _notice(dayzie, 15)
        assert open_tour_at(amber, FOUR, now=TWENTY_TO_FOUR) is None
        assert _starts(open_tours(TUESDAY, TUESDAY, now=TWENTY_TO_FOUR)) == [("Dayzie", "16:00"), ("Dayzie", "17:00")]
        booked = book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=TWENTY_TO_FOUR)
        assert booked.guide == dayzie

    def it_picks_the_fewest_tours_only_among_guides_whose_notice_allows(both: tuple[Member, Member]):
        amber, dayzie = both
        _notice(dayzie, 15)
        TourBookingFactory(guide=dayzie, starts_at=local(TUESDAY + timedelta(days=7), 16))
        # A day ahead both may give it, and Amber has fewer tours; 20 minutes out only Dayzie may.
        assert [tour.guide for tour in open_tours(TUESDAY, TUESDAY, now=NOW)] == [amber, amber]
        tour = tour_at(FOUR, now=TWENTY_TO_FOUR)
        assert tour is not None and tour.guide == dayzie

    def it_holds_later_visitors_to_the_notice_of_the_guide_who_has_the_time(both: tuple[Member, Member]):
        amber, dayzie = both
        _notice(dayzie, 15)
        book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW)
        assert FOUR not in [tour.starts_at for tour in open_tours(TUESDAY, TUESDAY, now=TWENTY_TO_FOUR)]
        with pytest.raises(TourUnavailableError):
            book_tour(guide=dayzie, starts_at=FOUR, visitor=_visitor(1, "kim@example.com"), now=TWENTY_TO_FOUR)

    def it_treats_tour_settings_notice_as_a_default_never_a_floor(amber: Member):
        house = FacilityTourSettings.load()
        assert TourGuideSettings.notice_minutes_by_guide([amber.pk], house) == {amber.pk: 24 * 60}
        assert open_tour_at(amber, FOUR, now=TWENTY_TO_FOUR) is None
        _notice(amber, 30)
        assert open_tour_at(amber, FOUR, now=TWENTY_TO_FOUR) is None
        assert open_tour_at(amber, FOUR, now=local(TUESDAY, 15, 30)) is not None
        _notice(amber, 48 * 60)
        house.min_notice_hours = 0
        house.save()
        assert open_tour_at(amber, FOUR, now=NOW) is None  # 31 hours out, under her own 48

    def it_lets_no_notice_book_right_up_to_the_start(amber: Member):
        _notice(amber, 0)
        assert open_tour_at(amber, FOUR, now=local(TUESDAY, 15, 59)) is not None
        assert open_tour_at(amber, FOUR, now=local(TUESDAY, 16, 1)) is None

    def it_never_binds_staff_or_the_kiosk(amber: Member):
        assert open_tour_at(amber, FOUR, now=TWENTY_TO_FOUR, staff=True) is not None
        assert open_tour_at(amber, FOUR, now=TWENTY_TO_FOUR, guide_notice=False) is not None
        kiosk = book_tour(
            guide=amber, starts_at=FOUR, visitor=_visitor(), source=TourBooking.Source.KIOSK, now=TWENTY_TO_FOUR
        )
        assert kiosk.guide == amber

    def it_reads_every_listed_guides_notice_in_one_query(amber: Member, dayzie: Member, django_assert_num_queries: Any):
        lee = make_guide(full_legal_name="Lee Long")
        _notice(dayzie, 15)
        TourGuideSettings.objects.create(guide=lee)  # a row, no notice of their own
        house = FacilityTourSettings.load()
        with django_assert_num_queries(1):
            notices = TourGuideSettings.notice_minutes_by_guide([amber.pk, dayzie.pk, lee.pk], house)
        assert notices == {amber.pk: 1440, dayzie.pk: 15, lee.pk: 1440}

    @pytest.mark.parametrize(
        ("minutes", "said"),
        [
            (0, "No notice"),
            (1, "1 minute"),
            (15, "15 minutes"),
            (60, "1 hour"),
            (2880, "48 hours"),
            (10080, "1 week"),
            (20160, "2 weeks"),
            (90, "90 minutes"),
        ],
    )
    def it_says_a_notice_in_words(minutes: int, said: str):
        assert notice_label(minutes) == said

    def it_names_the_guides_notice_on_a_request(amber: Member):
        request_ = TourBookingFactory(guide=amber, status=TourBooking.Status.PENDING)
        assert guide_notice(request_) == "24 hours"
        _notice(amber, 0)
        assert guide_notice(request_) is None
        assert guide_notice(TourBookingFactory(guide=amber)) is None


def describe_the_booking_notice_card():
    def it_shows_the_steps_with_the_house_default_first_and_picked(amber: Member):
        page = signed_in(amber).get(reverse("tours:hours")).content.decode()
        assert 'data-tour-notice-card data-autosave data-autosave-key="notice"' in page
        options = re.findall(
            r'<option value="([^"]*)"( selected)?>([^<]+)</option>', page.split("data-tour-notice-card")[1]
        )
        assert [(value, label) for value, _picked, label in options] == [
            ("", "House default (24 hours)"),
            ("0", "No notice, book me now"),
            ("15", "15 minutes"),
            ("30", "30 minutes"),
            ("60", "1 hour"),
            ("120", "2 hours"),
            ("240", "4 hours"),
            ("720", "12 hours"),
            ("1440", "24 hours"),
            ("2880", "48 hours"),
            ("10080", "1 week"),
            ("20160", "2 weeks"),
        ]
        assert options[0][1] == " selected"

    def it_saves_itself_and_goes_back_to_the_default(amber: Member):
        client = signed_in(amber)
        response = client.post(NOTICE, {"guide": str(amber.pk), "min_notice_minutes": "15"}, **AUTOSAVE)
        assert response.json() == {"saved": True, "rows": {}}
        assert TourGuideSettings.objects.get(guide=amber).min_notice_minutes == 15
        client.post(NOTICE, {"guide": str(amber.pk), "min_notice_minutes": ""}, **AUTOSAVE)
        assert TourGuideSettings.objects.get(guide=amber).min_notice_minutes is None

    def it_refuses_a_value_off_the_steps(amber: Member):
        response = signed_in(amber).post(NOTICE, {"guide": str(amber.pk), "min_notice_minutes": "17"}, **AUTOSAVE)
        assert response.status_code == 422 and list(response.json()["errors"]) == ["min_notice_minutes"]
        assert not TourGuideSettings.objects.filter(guide=amber).exists()

    def it_saves_without_the_script_with_a_flash(amber: Member):
        client = signed_in(amber)
        response = client.post(NOTICE, {"guide": str(amber.pk), "min_notice_minutes": "60"})
        assert response.status_code == 302
        assert TourGuideSettings.objects.get(guide=amber).min_notice_minutes == 60
        refused = client.post(NOTICE, {"guide": str(amber.pk), "min_notice_minutes": "17"}, follow=True)
        assert "Select a valid choice." in refused.content.decode()

    def it_lets_an_admin_set_a_guides_notice_and_no_other_guide(amber: Member, dayzie: Member, admin: Member):
        assert signed_in(admin).post(NOTICE, {"guide": str(amber.pk), "min_notice_minutes": "30"}).status_code == 302
        assert TourGuideSettings.objects.get(guide=amber).min_notice_minutes == 30
        assert signed_in(dayzie).post(NOTICE, {"guide": str(amber.pk), "min_notice_minutes": "0"}).status_code == 403

    def it_calls_the_house_setting_the_default(admin: Member):
        page = signed_in(admin).get(reverse("tours:settings")).content.decode()
        assert "Default minimum notice (hours)" in page
        assert "a guide who has not set their own notice on My Hours" in page


def describe_the_switch_is_gone():
    def it_has_no_let_guides_share_hours_setting(admin: Member):
        assert not hasattr(FacilityTourSettings.load(), "guides_share_hours")
        page = signed_in(admin).get(reverse("tours:settings")).content.decode()
        assert "guides_share_hours" not in page and "data-tour-settings-share" not in page


def describe_no_confirm_step():
    def it_books_a_website_visitor_at_once(amber: Member):
        booking = book_tour(guide=amber, starts_at=FOUR, visitor=_visitor(), now=NOW)
        assert booking.status == TourBooking.Status.BOOKED
