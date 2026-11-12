"""Confirm First (#1166): a guide's bookings wait as requests holding their seats until they confirm or decline."""

from __future__ import annotations

import threading
import time as clock
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.db import connection
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours import booking as booking_service
from tours.availability import open_tour_at
from tours.booking import (
    TourFullError,
    TourLockedError,
    Visitor,
    book_tour,
    cancel_tour_by_staff,
    cancel_tour_by_visitor,
    confirm_tour_request,
    decline_tour_request,
    reschedule_tour,
    reschedule_tour_by_staff,
)
from tours.models import FacilityTourSettings, TourBooking, TourGuideSettings
from tours.notify import request_url, tour_reminder_occurrences

pytestmark = pytest.mark.django_db

# 2030-01-07 is a Monday; tours run Tuesday the 8th, 4:00 and 5:00 PM.
NOW = local(date(2030, 1, 7), 9)
TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)
FIVE = local(TUESDAY, 17)
HTMX = {"HTTP_HX_REQUEST": "true"}
UPCOMING = reverse("tours:upcoming")
HOST = "tours.pastlives.space"


def _visitor(party: int = 2, email: str = "sam@example.com") -> Visitor:
    return Visitor(name="Sam Rivera", email=email, phone="", party_size=party)


def _confirms_first(guide: Member) -> None:
    TourGuideSettings.objects.update_or_create(
        guide=guide, defaults={"confirm_mode": TourGuideSettings.ConfirmMode.FIRST}
    )


def _flashes(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _to(address: str) -> list[Any]:
    return [message for message in mail.outbox if message.to == [address]]


@pytest.fixture
def hours(guide: Member) -> Any:
    return TourHoursFactory(guide=guide, seats=4)


@pytest.fixture
def first(guide: Member, hours: Any) -> Member:
    _confirms_first(guide)
    signed_in(guide)
    guide.refresh_from_db()
    return guide


@pytest.fixture
def request_(guide: Member) -> TourBooking:
    """A request on the guide's tour three days out, past every cutoff."""
    day = timezone.localdate() + timedelta(days=3)
    TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
    return TourBookingFactory(
        guide=guide,
        starts_at=local(day, 16),
        status=TourBooking.Status.PENDING,
        visitor_name="Sam Rivera",
        visitor_email="sam@example.com",
    )


def describe_the_guides_choice():
    def it_confirms_automatically_until_the_guide_picks(guide: Member):
        assert TourGuideSettings.confirm_mode_for(guide) == TourGuideSettings.ConfirmMode.AUTOMATIC
        assert not TourGuideSettings.confirms_first(guide)
        _confirms_first(guide)
        assert TourGuideSettings.confirms_first(guide)

    def it_names_the_guide_and_says_what_each_choice_means(guide: Member):
        chosen = TourGuideSettings(guide=guide)
        assert chosen.summary == "New bookings are confirmed at once, and you are told."
        chosen.confirm_mode = TourGuideSettings.ConfirmMode.FIRST
        assert chosen.summary == "Each new booking waits for you to confirm or decline it."
        assert str(chosen) == f"{guide} (Confirm First)"


def describe_booking_a_guide_who_confirms_first():
    def it_holds_the_seats_as_a_request_and_tells_both(first: Member, django_capture_on_commit_callbacks: Any):
        with django_capture_on_commit_callbacks(execute=True):
            booking = book_tour(guide=first, starts_at=FOUR, visitor=_visitor(3), now=NOW)
        assert booking.status == TourBooking.Status.PENDING and booking.is_request
        tour = open_tour_at(first, FOUR, now=NOW)
        assert tour is not None and tour.seats_left == 1
        (visitor,) = _to("sam@example.com")
        assert visitor.subject.startswith("Your Past Lives tour request:")
        assert visitor.attachments == []
        assert booking.self_serve_token in visitor.body
        (guide_email,) = _to(first.user.email)
        assert guide_email.subject.startswith("Tour request to confirm:")
        assert request_url(booking) in guide_email.body
        bell = Notification.objects.get(user=first.user)
        assert (bell.title, bell.url) == ("New tour request", f"{UPCOMING}#tour-booking-{booking.pk}")

    def it_refuses_a_party_the_held_seats_leave_no_room_for(first: Member):
        book_tour(guide=first, starts_at=FOUR, visitor=_visitor(3), now=NOW)
        with pytest.raises(TourFullError):
            book_tour(guide=first, starts_at=FOUR, visitor=_visitor(2, "kim@example.com"), now=NOW)

    def it_books_a_kiosk_walk_in_at_once(first: Member):
        booking = book_tour(guide=first, starts_at=FOUR, visitor=_visitor(), source=TourBooking.Source.KIOSK, now=NOW)
        assert booking.status == TourBooking.Status.BOOKED

    def it_books_at_once_for_a_guide_who_confirms_automatically(guide: Member, hours: Any):
        assert book_tour(guide=guide, starts_at=FOUR, visitor=_visitor(), now=NOW).status == TourBooking.Status.BOOKED


def describe_answering_a_request():
    def it_confirms_with_the_calendar_invite(first: Member, django_capture_on_commit_callbacks: Any):
        booking = book_tour(guide=first, starts_at=FOUR, visitor=_visitor(), now=NOW)
        with django_capture_on_commit_callbacks(execute=True):
            confirmed = confirm_tour_request(booking, actor=first.user, now=NOW)
        assert confirmed.status == TourBooking.Status.BOOKED
        (email,) = _to("sam@example.com")
        assert email.subject.startswith("Your Past Lives tour is confirmed:")
        assert "METHOD:REQUEST" in str(email.attachments[0][1])
        assert _to(first.user.email) == []

    def it_declines_with_the_note_and_frees_the_seats(first: Member, django_capture_on_commit_callbacks: Any):
        booking = book_tour(guide=first, starts_at=FOUR, visitor=_visitor(4), now=NOW)
        with django_capture_on_commit_callbacks(execute=True):
            declined = decline_tour_request(booking, actor=first.user, message="  Away that day.  ", now=NOW)
        assert (declined.status, declined.cancelled_by) == (TourBooking.Status.CANCELLED, first.user)
        tour = open_tour_at(first, FOUR, now=NOW)
        assert tour is not None and tour.seats_left == 4
        (email,) = _to("sam@example.com")
        assert email.subject.startswith("Your Past Lives tour request for")
        assert "Away that day." in email.body and email.attachments == []

    def it_refuses_an_answered_cancelled_or_started_request(first: Member):
        booking = book_tour(guide=first, starts_at=FOUR, visitor=_visitor(), now=NOW)
        confirm_tour_request(booking, actor=first.user, now=NOW)
        with pytest.raises(TourLockedError) as answered:
            decline_tour_request(booking, actor=first.user, now=NOW)
        assert answered.value.reason == "answered"
        cancelled = TourBookingFactory(guide=first, starts_at=FIVE, status=TourBooking.Status.CANCELLED)
        with pytest.raises(TourLockedError) as gone:
            confirm_tour_request(cancelled, actor=first.user, now=NOW)
        assert gone.value.reason == "cancelled"
        late = TourBookingFactory(guide=first, starts_at=FIVE, status=TourBooking.Status.PENDING)
        with pytest.raises(TourLockedError) as started:
            confirm_tour_request(late, actor=first.user, now=FIVE)
        assert started.value.reason == "past"


def describe_a_request_meanwhile():
    def it_can_be_cancelled_by_the_visitor_with_no_calendar_retraction(
        first: Member, django_capture_on_commit_callbacks: Any
    ):
        booking = book_tour(guide=first, starts_at=FOUR, visitor=_visitor(), now=NOW)
        with django_capture_on_commit_callbacks(execute=True):
            cancel_tour_by_visitor(booking, now=NOW - timedelta(days=3))
        (email,) = _to("sam@example.com")
        assert email.attachments == [] and "calendar update" not in email.body

    def it_waits_for_the_guide_before_anyone_moves_or_cancels_it(first: Member):
        booking = book_tour(guide=first, starts_at=FOUR, visitor=_visitor(), now=NOW)
        with pytest.raises(TourLockedError) as visitor:
            reschedule_tour(booking, guide=first, starts_at=FIVE, now=NOW - timedelta(days=3))
        with pytest.raises(TourLockedError) as staff_move:
            reschedule_tour_by_staff(booking, guide=first, starts_at=FIVE, actor=first.user, now=NOW)
        with pytest.raises(TourLockedError) as staff_cancel:
            cancel_tour_by_staff(booking, actor=first.user, now=NOW)
        assert {visitor.value.reason, staff_move.value.reason, staff_cancel.value.reason} == {"request"}

    def it_lists_on_upcoming_never_on_past_and_gets_no_reminder(first: Member):
        booking = book_tour(guide=first, starts_at=FOUR, visitor=_visitor(), now=NOW)
        assert list(TourBooking.objects.upcoming(NOW)) == [booking]
        assert list(TourBooking.objects.past(FIVE)) == []
        assert list(tour_reminder_occurrences(FOUR - timedelta(hours=24))) == []


def describe_the_tours_page():
    def it_offers_confirm_and_decline_on_upcoming(guide: Member, request_: TourBooking):
        page = signed_in(guide).get(UPCOMING).content.decode()
        assert f'id="tour-booking-{request_.pk}"' in page
        assert f'data-tour-confirm="{request_.pk}"' in page and f'data-tour-decline="{request_.pk}"' in page
        assert f'data-tour-reschedule="{request_.pk}"' not in page

    def it_confirms_from_the_row(guide: Member, request_: TourBooking):
        response = signed_in(guide).post(reverse("tours:booking_confirm", args=[request_.pk]), **HTMX)
        assert (response.status_code, response["HX-Redirect"]) == (204, UPCOMING)
        assert _flashes(response)[0].endswith("is confirmed. We emailed them.")
        request_.refresh_from_db()
        assert request_.status == TourBooking.Status.BOOKED
        again = signed_in(guide).post(reverse("tours:booking_confirm", args=[request_.pk]), **HTMX)
        assert _flashes(again) == ["This request was already answered."]

    def it_declines_from_the_confirm_with_a_message(guide: Member, request_: TourBooking):
        url = reverse("tours:booking_decline", args=[request_.pk])
        too_long = signed_in(guide).post(url, {"message": "x" * 2001}, **HTMX)
        assert _flashes(too_long)[0].startswith("Ensure this value has at most")
        response = signed_in(guide).post(url, {"message": "Away."}, **HTMX)
        assert _flashes(response)[0].endswith("is declined. We emailed them.")
        request_.refresh_from_db()
        assert request_.status == TourBooking.Status.CANCELLED
        again = signed_in(guide).post(url, **HTMX)
        assert _flashes(again) == ["This tour was already cancelled."]

    def it_keeps_other_guides_off(request_: TourBooking):
        other = signed_in(make_guide(full_legal_name="Olive Other"))
        assert other.post(reverse("tours:booking_confirm", args=[request_.pk])).status_code == 403
        assert other.post(reverse("tours:booking_decline", args=[request_.pk])).status_code == 403


def describe_the_confirming_bookings_card():
    def it_shows_the_choice_and_saves_the_guides_own(guide: Member):
        client = signed_in(guide)
        page = client.get(reverse("tours:hours")).content.decode()
        assert 'checked data-tour-confirm-choice="automatic"' in page
        response = client.post(reverse("tours:confirm_mode_save"), {"confirm_mode": "first"})
        assert (response.status_code, response["Location"]) == (302, reverse("tours:hours"))
        assert _flashes(response) == ["Saved. Each new booking waits for you to confirm or decline it."]
        assert TourGuideSettings.confirms_first(guide)
        assert 'checked data-tour-confirm-choice="first"' in client.get(reverse("tours:hours")).content.decode()

    def it_lets_an_admin_set_a_guides_and_refuses_a_bad_choice(admin: Member, guide: Member):
        client = signed_in(admin)
        url = reverse("tours:confirm_mode_save")
        bad = client.post(url, {"guide": guide.pk, "confirm_mode": "never"})
        assert _flashes(bad) == ["Pick Confirm First or Confirm Automatically."]
        assert bad["Location"] == f"{reverse('tours:hours')}?guide={guide.pk}"
        client.post(url, {"guide": guide.pk, "confirm_mode": "first"})
        assert TourGuideSettings.confirms_first(guide)

    def it_refuses_a_guide_setting_someone_elses(guide: Member):
        other = make_guide(full_legal_name="Olive Other")
        response = signed_in(guide).post(
            reverse("tours:confirm_mode_save"), {"guide": other.pk, "confirm_mode": "first"}
        )
        assert response.status_code == 403
        assert not TourGuideSettings.confirms_first(other)


def describe_the_visitors_page():
    def it_says_the_request_waits_and_offers_only_cancel(request_: TourBooking):
        client = Client(HTTP_HOST=HOST)
        token = request_.self_serve_token
        page = client.get(f"{reverse('tours_public:manage', args=[token])}?booked=1").content.decode()
        assert "Request Sent" in page and "data-tour-requested" in page
        assert "data-tour-cancel" in page and "data-tour-reschedule" not in page
        later = client.get(reverse("tours_public:manage", args=[token])).content.decode()
        assert "Your Tour Request" in later
        refused = client.get(reverse("tours_public:reschedule", args=[token])).content.decode()
        assert 'data-tour-reason="request"' in refused and "data-tour-request-wait" in refused


def describe_two_visitors_racing_for_a_confirm_first_guides_last_seats():
    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_lets_one_request_and_tells_the_other_it_filled_up():
        guide = make_guide(full_legal_name="Gina Guide")
        TourHoursFactory(guide=guide, seats=2)
        _confirms_first(guide)
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
                book_tour(guide=guide, starts_at=FOUR, visitor=_visitor(2, f"v{number}@example.com"), now=NOW)
                outcomes.append("requested")
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
        assert sorted(outcomes) == ["filled", "filled", "filled", "requested"]
        assert TourBooking.objects.filter(status=TourBooking.Status.PENDING).count() == 1
        assert TourBooking.objects.seats_taken(guide, FOUR) == 2
