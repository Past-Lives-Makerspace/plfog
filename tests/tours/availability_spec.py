"""Open facility tour times, computed live from the guides' hours (#947)."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from membership.models import Member
from tests.membership.factories import MemberFactory
from tests.tours.conftest import make_guide
from tests.tours.factories import TourBookingFactory, TourDayOffFactory, TourHoursFactory, local
from tours.availability import BookingWindow, OpenTour, open_tour_at, open_tours
from tours.models import FacilityTourSettings, TourBooking, TourHours

pytestmark = pytest.mark.django_db

# 2030-01-07 is a Monday; the next Tuesday is the 8th.
MONDAY = date(2030, 1, 7)
TUESDAY = date(2030, 1, 8)
NOW = local(MONDAY, 9)


def _settings(**values: int) -> FacilityTourSettings:
    tour_settings = FacilityTourSettings.load()
    for name, value in values.items():
        setattr(tour_settings, name, value)
    tour_settings.save()
    return tour_settings


def _starts(tours: list[OpenTour]) -> list[tuple[str, str]]:
    return [(tour.guide.full_legal_name, f"{tour.starts_at:%Y-%m-%d %H:%M}") for tour in tours]


def describe_open_tours():
    def it_carves_the_hours_into_tours_with_their_seats(guide: Member):
        TourHoursFactory(guide=guide)
        tours = open_tours(MONDAY, TUESDAY, now=NOW)
        assert [(tour.starts_at, tour.ends_at, tour.seats_left) for tour in tours] == [
            (local(TUESDAY, 16), local(TUESDAY, 17), 4),
            (local(TUESDAY, 17), local(TUESDAY, 18), 4),
        ]

    def it_leaves_no_partial_tour_at_the_end_of_the_window(guide: Member):
        TourHoursFactory(guide=guide, end_time=time(18, 30), tour_minutes=90)
        assert [tour.starts_at for tour in open_tours(TUESDAY, TUESDAY, now=NOW)] == [local(TUESDAY, 16)]

    def it_offers_two_tours_when_two_guides_share_an_hour(guide: Member):
        other = make_guide(full_legal_name="Ana Other")
        TourHoursFactory(guide=guide)
        TourHoursFactory(guide=other)
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW)) == [
            ("Ana Other", "2030-01-08 16:00"),
            ("Gina Guide", "2030-01-08 16:00"),
            ("Ana Other", "2030-01-08 17:00"),
            ("Gina Guide", "2030-01-08 17:00"),
        ]

    def it_lists_one_guide_alone_when_asked(guide: Member):
        TourHoursFactory(guide=guide)
        TourHoursFactory(guide=make_guide())
        assert {tour.guide for tour in open_tours(TUESDAY, TUESDAY, guide=guide, now=NOW)} == {guide}

    def it_drops_a_tour_inside_the_minimum_notice(guide: Member):
        TourHoursFactory(guide=guide)
        # Monday 5 PM: Tuesday 4 PM is 23 hours away, Tuesday 5 PM exactly 24.
        assert [tour.starts_at for tour in open_tours(MONDAY, TUESDAY, now=local(MONDAY, 17))] == [local(TUESDAY, 17)]
        _settings(min_notice_hours=0)
        assert len(open_tours(MONDAY, TUESDAY, now=local(MONDAY, 17))) == 2

    def it_drops_every_tour_past_the_horizon(guide: Member):
        TourHoursFactory(guide=guide)
        far = MONDAY + timedelta(days=90)
        tuesdays = sorted({tour.starts_at.date() for tour in open_tours(MONDAY, far, now=NOW)})
        # 30 days from Monday the 7th is Wednesday Feb 6, so the last Tuesday is Feb 5.
        assert tuesdays[0] == TUESDAY
        assert tuesdays[-1] == date(2030, 2, 5)

    def it_follows_a_horizon_change_at_once(guide: Member):
        TourHoursFactory(guide=guide)
        far = MONDAY + timedelta(days=60)
        assert len(open_tours(MONDAY, far, now=NOW)) == 10
        _settings(booking_horizon_days=7)
        assert {tour.starts_at.date() for tour in open_tours(MONDAY, far, now=NOW)} == {TUESDAY}

    def it_skips_the_days_a_guide_is_off(guide: Member):
        TourHoursFactory(guide=guide)
        other = make_guide(full_legal_name="Ana Other")
        TourHoursFactory(guide=other)
        TourDayOffFactory(guide=guide, starts_on=TUESDAY, ends_on=TUESDAY + timedelta(days=7))
        tours = open_tours(TUESDAY, TUESDAY + timedelta(days=14), now=NOW)
        assert {(tour.guide.pk, tour.starts_at.date()) for tour in tours} == {
            (other.pk, TUESDAY),
            (other.pk, TUESDAY + timedelta(days=7)),
            (other.pk, TUESDAY + timedelta(days=14)),
            (guide.pk, TUESDAY + timedelta(days=14)),
        }

    def it_counts_the_seats_booked_and_attended_parties_hold(guide: Member):
        TourHoursFactory(guide=guide)
        TourBookingFactory(guide=guide, starts_at=local(TUESDAY, 16), party_size=2)
        TourBookingFactory(guide=guide, starts_at=local(TUESDAY, 17), party_size=1, status=TourBooking.Status.ATTENDED)
        for status in (TourBooking.Status.CANCELLED, TourBooking.Status.NO_SHOW):
            TourBookingFactory(guide=guide, starts_at=local(TUESDAY, 17), party_size=3, status=status)
        assert [tour.seats_left for tour in open_tours(TUESDAY, TUESDAY, now=NOW)] == [2, 3]

    def it_keeps_another_guides_booking_off_this_guides_tour(guide: Member):
        TourHoursFactory(guide=guide)
        TourBookingFactory(starts_at=local(TUESDAY, 16), party_size=4)
        assert [tour.seats_left for tour in open_tours(TUESDAY, TUESDAY, now=NOW)] == [4, 4]

    def it_drops_a_full_tour_unless_asked_for_it(guide: Member):
        TourHoursFactory(guide=guide)
        TourBookingFactory(guide=guide, starts_at=local(TUESDAY, 16), party_size=4)
        assert [tour.starts_at for tour in open_tours(TUESDAY, TUESDAY, now=NOW)] == [local(TUESDAY, 17)]
        full = open_tours(TUESDAY, TUESDAY, include_full=True, now=NOW)[0]
        assert (full.seats_left, full.is_full) == (0, True)

    def it_never_counts_below_zero_seats_left(guide: Member):
        TourHoursFactory(guide=guide, seats=2)
        TourBookingFactory(guide=guide, starts_at=local(TUESDAY, 16), party_size=3)
        assert open_tours(TUESDAY, TUESDAY, include_full=True, now=NOW)[0].seats_left == 0

    def it_offers_admins_hours_and_nobody_elses_without_the_permission():
        admin = MemberFactory(full_legal_name="Ada Admin", fog_role=Member.FogRole.ADMIN)
        superuser = User.objects.create_superuser("root", "root@example.com", "pass")
        TourHoursFactory(guide=admin)
        TourHoursFactory(guide=superuser.member, weekday=TourHours.Weekday.WEDNESDAY)
        TourHoursFactory(guide=MemberFactory(full_legal_name="No Permission"))
        TourHoursFactory(guide=make_guide(status=Member.Status.FORMER))
        assert {tour.guide for tour in open_tours(MONDAY, MONDAY + timedelta(days=2), now=NOW)} == {
            admin,
            superuser.member,
        }

    def it_stops_offering_a_guide_whose_permission_is_revoked(guide: Member):
        TourHoursFactory(guide=guide)
        guide.admin_capabilities.all().delete()
        assert open_tours(TUESDAY, TUESDAY, now=NOW) == []

    def it_offers_a_tour_once_when_two_of_a_guides_rows_yield_it(guide: Member):
        TourHoursFactory(guide=guide)
        TourHoursFactory(guide=guide, start_time=time(16, 0), end_time=time(17, 0))
        assert len(open_tours(TUESDAY, TUESDAY, now=NOW)) == 2

    def it_skips_days_before_today_and_an_empty_range(guide: Member):
        TourHoursFactory(guide=guide)
        assert open_tours(TUESDAY - timedelta(days=14), TUESDAY - timedelta(days=7), now=NOW) == []
        assert open_tours(TUESDAY, MONDAY, now=NOW) == []

    def it_keeps_the_wall_clock_grid_across_a_daylight_saving_change(guide: Member):
        # 2030-03-10 is the Sunday clocks go forward in Portland.
        TourHoursFactory(guide=guide, weekday=TourHours.Weekday.SUNDAY, start_time=time(9, 0), end_time=time(12, 0))
        sunday = date(2030, 3, 10)
        tours = open_tours(sunday, sunday, now=local(date(2030, 3, 1), 9))
        assert [f"{tour.starts_at.astimezone(local(sunday, 9).tzinfo):%H:%M}" for tour in tours] == [
            "09:00",
            "10:00",
            "11:00",
        ]

    def it_costs_four_queries_however_long_the_range(guide: Member, django_assert_num_queries):
        TourHoursFactory(guide=guide)
        TourDayOffFactory(guide=guide, starts_on=TUESDAY)
        TourBookingFactory(guide=guide, starts_at=local(TUESDAY + timedelta(days=7), 16))
        FacilityTourSettings.load()
        with django_assert_num_queries(4):
            open_tours(MONDAY, MONDAY + timedelta(days=30), now=NOW)

    def it_asks_nothing_more_once_no_guide_has_hours(django_assert_num_queries):
        FacilityTourSettings.load()
        with django_assert_num_queries(2):
            assert open_tours(MONDAY, TUESDAY, now=NOW) == []


def describe_open_tour_at():
    def it_finds_one_tour_with_its_seats_taken(guide: Member):
        hours = TourHoursFactory(guide=guide)
        TourBookingFactory(guide=guide, starts_at=local(TUESDAY, 17), party_size=3)
        tour = open_tour_at(guide, local(TUESDAY, 17), now=NOW)
        assert tour == OpenTour(
            guide=guide,
            hours_id=hours.pk,
            starts_at=local(TUESDAY, 17),
            ends_at=local(TUESDAY, 18),
            seats=4,
            seats_taken=3,
        )
        assert tour is not None and tour.seats_left == 1

    def it_returns_a_full_tour_so_a_booking_page_can_say_it_filled(guide: Member):
        TourHoursFactory(guide=guide)
        TourBookingFactory(guide=guide, starts_at=local(TUESDAY, 16), party_size=4)
        tour = open_tour_at(guide, local(TUESDAY, 16), now=NOW)
        assert tour is not None and tour.is_full

    def it_returns_none_for_a_start_the_hours_never_yield(guide: Member):
        TourHoursFactory(guide=guide)
        assert open_tour_at(guide, local(TUESDAY, 16, 30), now=NOW) is None
        assert open_tour_at(guide, local(TUESDAY + timedelta(days=1), 16), now=NOW) is None

    def it_returns_none_on_a_day_off(guide: Member):
        TourHoursFactory(guide=guide)
        TourDayOffFactory(guide=guide, starts_on=TUESDAY - timedelta(days=1), ends_on=TUESDAY)
        assert open_tour_at(guide, local(TUESDAY, 16), now=NOW) is None

    def it_returns_none_inside_the_notice_or_past_the_horizon(guide: Member):
        TourHoursFactory(guide=guide)
        assert open_tour_at(guide, local(TUESDAY, 16), now=local(MONDAY, 17)) is None
        assert open_tour_at(guide, local(TUESDAY + timedelta(days=35), 16), now=NOW) is None

    def it_returns_none_for_someone_who_gives_no_tours():
        member = MemberFactory()
        TourHoursFactory(guide=member)
        assert open_tour_at(member, local(TUESDAY, 16), now=NOW) is None

    def it_locks_inside_a_transaction(guide: Member):
        TourHoursFactory(guide=guide)
        with transaction.atomic():
            tour = open_tour_at(guide, local(TUESDAY, 16), lock=True, now=NOW)
        assert tour is not None and tour.seats_left == 4

    @pytest.mark.django_db(transaction=True)
    def it_refuses_to_lock_outside_a_transaction():
        guide = make_guide()
        with pytest.raises(RuntimeError, match="transaction.atomic"):
            open_tour_at(guide, local(TUESDAY, 16), lock=True, now=NOW)

    @pytest.mark.postgres_only
    def it_takes_a_row_lock_on_the_hours_that_yield_the_tour(guide: Member):
        TourHoursFactory(guide=guide)
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            open_tour_at(guide, local(TUESDAY, 16), lock=True, now=NOW)
        locked = [query["sql"] for query in queries.captured_queries if "FOR UPDATE" in query["sql"]]
        assert len(locked) == 1
        assert '"tours_tourhours"' in locked[0]


def describe_booking_window():
    def it_runs_from_today_to_the_horizon_and_starts_after_the_notice():
        window = BookingWindow.at(NOW, FacilityTourSettings.load())
        assert (window.first_day, window.last_day, window.earliest_start) == (
            MONDAY,
            MONDAY + timedelta(days=30),
            NOW + timedelta(hours=24),
        )
        assert window.admits(local(MONDAY + timedelta(days=30), 21))
        assert not window.admits(local(MONDAY + timedelta(days=31), 9))
