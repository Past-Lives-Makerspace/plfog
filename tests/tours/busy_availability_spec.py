"""A guide's Google Calendar busy times hide tour times from visitors (#951), and only from visitors."""

from __future__ import annotations

from datetime import UTC, date, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from django.test import Client
from django.utils import timezone

from membership.models import Member
from tests.tours.factories import TourBookingFactory, TourCalendarConnectionFactory, TourHoursFactory, local
from tours import google
from tours.availability import open_tour_at, open_tours
from tours.booking import TourUnavailableError, Visitor, book_tour
from tours.models import TourBooking, TourCalendarConnection

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("google_on")]

# 2030-01-07 is a Monday; the guide's hours give a 4:00 and a 5:00 PM tour on Tuesday the 8th.
NOW = local(date(2030, 1, 7), 9)
TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)
FIVE = local(TUESDAY, 17)
HOST = "tours.pastlives.space"


@pytest.fixture
def hours(guide: Member) -> Any:
    return TourHoursFactory(guide=guide, seats=4)


@pytest.fixture
def busy_at_four_thirty(guide: Member) -> TourCalendarConnection:
    """The guide's calendar shows them busy from 4:30 to 4:45 PM: inside the 4:00 tour only."""
    start = local(TUESDAY, 16, 30).astimezone(UTC)
    return TourCalendarConnectionFactory(
        guide=guide,
        busy_spans=[[start.isoformat(), (start + timedelta(minutes=15)).isoformat()]],
        busy_checked_at=timezone.now(),
    )


def _starts(tours: list[Any]) -> list[Any]:
    return [tour.starts_at for tour in tours]


def describe_open_tours():
    def it_hides_the_busy_tour_and_no_other(hours: Any, busy_at_four_thirty: TourCalendarConnection):
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW)) == [FIVE.astimezone(UTC)]

    def it_still_offers_staff_every_tour(hours: Any, busy_at_four_thirty: TourCalendarConnection):
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW, staff=True)) == [
            FOUR.astimezone(UTC),
            FIVE.astimezone(UTC),
        ]

    def it_offers_everything_once_the_connection_breaks(hours: Any, busy_at_four_thirty: TourCalendarConnection):
        busy_at_four_thirty.status = TourCalendarConnection.Status.BROKEN
        busy_at_four_thirty.save()
        assert len(open_tours(TUESDAY, TUESDAY, now=NOW)) == 2

    def it_offers_everything_while_google_is_not_set_up(
        hours: Any, busy_at_four_thirty: TourCalendarConnection, settings
    ):
        settings.GOOGLE_OAUTH_CLIENT_ID = ""
        assert len(open_tours(TUESDAY, TUESDAY, now=NOW)) == 2

    def it_lets_a_busy_time_that_only_touches_a_tour_pass(guide: Member, hours: Any):
        end_of_four = local(TUESDAY, 17).astimezone(UTC)
        TourCalendarConnectionFactory(
            guide=guide,
            busy_spans=[[end_of_four.isoformat(), (end_of_four + timedelta(hours=1)).isoformat()]],
            busy_checked_at=timezone.now(),
        )
        assert _starts(open_tours(TUESDAY, TUESDAY, now=NOW)) == [FOUR.astimezone(UTC)]


def describe_open_tour_at():
    def it_refuses_a_visitor_the_busy_tour(guide: Member, hours: Any, busy_at_four_thirty: TourCalendarConnection):
        assert open_tour_at(guide, FOUR, now=NOW) is None
        assert open_tour_at(guide, FIVE, now=NOW) is not None

    def it_gives_staff_the_busy_tour(guide: Member, hours: Any, busy_at_four_thirty: TourCalendarConnection):
        assert open_tour_at(guide, FOUR, now=NOW, staff=True) is not None

    def it_refuses_a_booking_posted_for_the_busy_time(
        guide: Member, hours: Any, busy_at_four_thirty: TourCalendarConnection
    ):
        visitor = Visitor(name="Sam Rivera", email="sam@example.com", phone="", party_size=1)
        with pytest.raises(TourUnavailableError):
            book_tour(guide=guide, starts_at=FOUR, visitor=visitor, now=NOW)

    def it_never_takes_away_a_tour_already_booked(guide: Member, hours: Any):
        booked = TourBookingFactory(guide=guide, starts_at=FOUR, party_size=2)
        start = FOUR.astimezone(UTC)
        TourCalendarConnectionFactory(
            guide=guide,
            busy_spans=[[start.isoformat(), (start + timedelta(hours=1)).isoformat()]],
            busy_checked_at=timezone.now(),
        )
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.BOOKED
        assert TourBooking.objects.seats_taken(guide, FOUR) == 2


def describe_the_booking_page():
    @pytest.fixture
    def soon(guide: Member) -> date:
        day = timezone.localdate() + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        return day

    @respx.mock
    def it_reads_stale_busy_times_first_and_hides_the_busy_tour(guide: Member, soon: date):
        connection = TourCalendarConnectionFactory(guide=guide, busy_checked_at=timezone.now() - timedelta(minutes=20))
        start = local(soon, 16)
        route = respx.get(f"{google.API_URL}/calendars/primary/events").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "start": {"dateTime": start.isoformat()},
                            "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
                        }
                    ]
                },
            )
        )
        response = Client(HTTP_HOST=HOST).get("/", {"date": f"{soon:%Y-%m-%d}"})
        assert response.status_code == 200
        assert route.called
        connection.refresh_from_db()
        assert connection.busy_checked_at > timezone.now() - timedelta(minutes=1)
        content = response.content.decode()
        five_key = f"{guide.pk}-{local(soon, 17).astimezone(UTC):%Y%m%dT%H%MZ}"
        four_key = f"{guide.pk}-{start.astimezone(UTC):%Y%m%dT%H%MZ}"
        assert five_key in content and four_key not in content

    @respx.mock
    def it_lists_tours_from_the_cache_when_google_is_down(guide: Member, soon: date):
        TourCalendarConnectionFactory(guide=guide, busy_checked_at=timezone.now() - timedelta(minutes=20))
        respx.get(f"{google.API_URL}/calendars/primary/events").mock(side_effect=httpx.ConnectTimeout("slow"))
        response = Client(HTTP_HOST=HOST).get("/", {"date": f"{soon:%Y-%m-%d}"})
        assert response.status_code == 200
        assert f"{guide.pk}-{local(soon, 16).astimezone(UTC):%Y%m%dT%H%MZ}" in response.content.decode()

    def it_never_shows_an_error_page_for_a_sync_bug(guide: Member, soon: date):
        with patch("tours.public_views.refresh_stale_busy", side_effect=RuntimeError("bug")):
            response = Client(HTTP_HOST=HOST).get("/", {"date": f"{soon:%Y-%m-%d}"})
        assert response.status_code == 200
