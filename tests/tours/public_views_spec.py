"""The visitor's pages on tours.pastlives.space (#948): booking, the email links, and the host's walls."""

from __future__ import annotations

import re
from datetime import UTC, date, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.availability import open_tour_at
from tours.forms import CREATE_ACCOUNT_LABEL
from tours.models import FacilityTourSettings, TourBooking

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    """Each spec starts with the booking POST's rate limit unspent (its counters live in the cache)."""
    cache.clear()


HOST = "tours.pastlives.space"
BOOK = reverse("tours_public:book")


def _client(**kwargs: Any) -> Client:
    return Client(HTTP_HOST=HOST, **kwargs)


def _day() -> date:
    """A day far enough off to clear the 24 hour notice and the cancel cutoff."""
    return timezone.localdate() + timedelta(days=4)


def _key(guide: Member, hour: int) -> str:
    return f"{guide.pk}-{local(_day(), hour).astimezone(UTC):%Y%m%dT%H%MZ}"


@pytest.fixture
def hours(guide: Member) -> Any:
    return TourHoursFactory(guide=guide, weekday=_day().weekday(), seats=4)


def _post(guide: Member, hour: int = 16, **values: str) -> dict[str, str]:
    data = {"tour": _key(guide, hour), "name": "Sam Rivera", "email": "sam@example.com", "phone": "", "party_size": "2"}
    data.update(values)
    return data


def describe_the_host():
    def it_serves_the_booking_page_at_the_root(guide: Member, hours: Any):
        response = _client().get("/")
        assert response.status_code == 200
        assert 'data-tour-page="book"' in response.content.decode()

    def it_404s_members_and_class_site_paths(guide: Member):
        client = _client()
        for path in ("/members/", "/classes/", "/facility-tours/", "/guilds/", "/account/", "/settings/"):
            assert client.get(path).status_code == 404, path

    def it_serves_sign_in_so_a_visitor_can_have_their_details_filled_in():
        assert _client().get(reverse("account_login")).status_code == 200

    def it_404s_the_tour_pages_on_the_other_hosts(guide: Member, hours: Any, settings: Any):
        settings.ALLOWED_HOSTS = ["*"]
        booking = TourBookingFactory(guide=guide)
        paths = [
            BOOK,
            reverse("tours_public:manage", args=[booking.self_serve_token]),
            reverse("tours_public:reschedule", args=[booking.self_serve_token]),
            reverse("tours_public:cancel", args=[booking.self_serve_token]),
        ]
        for host in ("members.pastlives.space", "classes.pastlives.space", "guilds.pastlives.space", "localhost"):
            for path in paths:
                assert Client(HTTP_HOST=host).get(path).status_code == 404, (host, path)


def describe_booking_page():
    def it_lists_the_days_and_the_picked_days_times_with_seats_left(guide: Member, hours: Any):
        TourBookingFactory(guide=guide, starts_at=local(_day(), 16), party_size=3)
        content = _client().get(f"/?date={_day():%Y-%m-%d}").content.decode()
        assert f'aria-current="date" data-tour-day="{_day():%Y-%m-%d}"' in content
        assert f'data-tour-time="{_key(guide, 16)}"' in content
        assert re.search(r"4:00 PM</span>\s*<span class=\"pl-tourpub__time-seats\">1 seat left</span>", content)
        assert re.search(r"5:00 PM</span>\s*<span class=\"pl-tourpub__time-seats\">4 seats left</span>", content)

    def it_names_no_guide_for_a_blank_name_when_two_share_a_time(guide: Member, hours: Any):
        other = make_guide(full_legal_name="", preferred_name="")
        TourHoursFactory(guide=other, weekday=_day().weekday())
        response = _client().get(f"/?date={_day():%Y-%m-%d}")
        assert response.status_code == 200
        assert "4 seats left</span>" in response.content.decode()

    def it_names_the_guide_when_two_share_a_time(guide: Member, hours: Any):
        other = make_guide(full_legal_name="Olive Other")
        TourHoursFactory(guide=other, weekday=_day().weekday())
        content = _client().get(f"/?date={_day():%Y-%m-%d}").content.decode()
        assert "4 seats left with Olive</span>" in content
        assert "4 seats left with Gina</span>" in content

    def it_picks_the_first_day_for_an_unknown_date(guide: Member, hours: Any):
        content = _client().get("/?date=not-a-date").content.decode()
        assert f'aria-current="date" data-tour-day="{_day():%Y-%m-%d}"' in content

    def it_says_so_when_no_time_is_open():
        assert "data-tour-empty>No tour times are open right now." in _client().get("/").content.decode()

    def it_shows_the_closed_message_while_not_accepting(guide: Member, hours: Any):
        tour_settings = FacilityTourSettings.load()
        tour_settings.accepting_bookings = False
        tour_settings.closed_message = "Back in the spring."
        tour_settings.save()
        content = _client().get("/").content.decode()
        assert "data-tour-closed>Back in the spring.</p>" in content
        assert "data-tour-time" not in content

    def it_opens_the_form_for_a_picked_time_with_one_to_the_seats_left(guide: Member, hours: Any):
        TourBookingFactory(guide=guide, starts_at=local(_day(), 16), party_size=1)
        content = _client().get(f"/?tour={_key(guide, 16)}").content.decode()
        assert f'data-tour-chosen="{_key(guide, 16)}"' in content
        assert '<option value="3">3</option>' in content
        assert '<option value="4">' not in content
        assert CREATE_ACCOUNT_LABEL in content

    def it_starts_the_account_toggle_on(guide: Member, hours: Any):
        content = _client().get(f"/?tour={_key(guide, 16)}").content.decode()
        assert re.search(r'<input[^>]*name="create_account"[^>]*checked', content)

    def it_fills_in_a_signed_in_visitors_name_and_email_and_offers_no_account(guide: Member, hours: Any):
        member = make_guide(full_legal_name="Lee Member")
        client = signed_in(member)
        member.refresh_from_db()
        member.user.first_name, member.user.last_name = "Lee", "Member"
        member.user.save()
        content = client.get(f"/?tour={_key(guide, 16)}", HTTP_HOST=HOST).content.decode()
        assert 'value="Lee Member"' in content
        assert f'value="{member.user.email}"' in content
        assert CREATE_ACCOUNT_LABEL not in content

    def it_falls_back_to_the_members_name_for_a_user_without_one(guide: Member, hours: Any):
        member = make_guide(full_legal_name="Nina Noname")
        client = signed_in(member)
        member.refresh_from_db()
        User.objects.filter(pk=member.user.pk).update(first_name="", last_name="")
        assert (
            f'value="{member.display_name}"' in client.get(f"/?tour={_key(guide, 16)}", HTTP_HOST=HOST).content.decode()
        )

    def it_shows_the_times_for_a_tour_key_that_is_not_open(guide: Member, hours: Any):
        content = _client().get(f"/?tour={guide.pk}-20200101T0000Z").content.decode()
        assert "data-tour-form" not in content
        assert "data-tour-time" in content


def describe_booking():
    def it_books_and_lands_on_the_confirmation(guide: Member, hours: Any):
        response = _client().post("/", _post(guide))
        booking = TourBooking.objects.get()
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('tours_public:manage', args=[booking.self_serve_token])}?booked=1"
        assert (booking.party_size, booking.starts_at, booking.user) == (2, local(_day(), 16), None)
        tour = open_tour_at(guide, local(_day(), 16))
        assert tour is not None and tour.seats_left == 2
        content = _client().get(response["Location"]).content.decode()
        assert "You're Booked</h1>" in content
        assert "data-tour-confirmed>We emailed sam@example.com a confirmation" in content
        assert "2808 SE 9th Ave, Portland, OR 97202" in content
        assert "data-tour-reschedule>Reschedule</a>" in content
        assert "data-tour-cancel>Cancel My Tour</a>" in content

    def it_makes_a_guest_account_when_asked(guide: Member, hours: Any):
        _client().post("/", _post(guide, email="fresh@example.com", create_account="on"))
        booking = TourBooking.objects.get()
        assert booking.user is not None
        assert Member.objects.get(user=booking.user).status == Member.Status.GUEST

    def it_creates_no_account_with_the_toggle_off(guide: Member, hours: Any):
        users = User.objects.count()
        _client().post("/", _post(guide, email="fresh@example.com"))
        assert User.objects.count() == users

    def it_links_a_signed_in_visitor(guide: Member, hours: Any):
        member = make_guide(full_legal_name="Lee Member")
        client = signed_in(member)
        member.refresh_from_db()
        client.post("/", _post(guide, email="whatever@example.com", create_account="on"), HTTP_HOST=HOST)
        assert TourBooking.objects.get().user == member.user

    def it_says_the_time_filled_up_instead_of_overbooking(guide: Member, hours: Any):
        TourBookingFactory(guide=guide, starts_at=local(_day(), 16), party_size=4)
        response = _client().post("/", _post(guide, party_size="2"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "data-tour-error>That time just filled up, pick another.</div>" in content
        assert "data-tour-time" in content
        assert TourBooking.objects.count() == 1

    def it_says_how_many_seats_are_left_for_a_party_too_big(guide: Member, hours: Any):
        TourBookingFactory(guide=guide, starts_at=local(_day(), 16), party_size=3)
        content = _client().post("/", _post(guide, party_size="2")).content.decode()
        assert "data-tour-error>Only 1 seat is left at that time.</div>" in content
        assert TourBooking.objects.count() == 1

    def it_lands_a_second_press_of_book_on_the_first_booking(guide: Member, hours: Any):
        first = _client().post("/", _post(guide))
        second = _client().post("/", _post(guide))
        assert TourBooking.objects.count() == 1
        assert second["Location"] == first["Location"]

    def it_books_nothing_for_a_filled_honeypot(guide: Member, hours: Any):
        response = _client().post("/", _post(guide, website="https://spam.example"))
        assert (response.status_code, response["Location"]) == (302, "/")
        assert not TourBooking.objects.exists()

    def it_turns_away_a_connection_past_its_hourly_limit(guide: Member, hours: Any):
        from tours.public_views import BOOKING_HOURLY_LIMIT

        for number in range(BOOKING_HOURLY_LIMIT):
            _client().post("/", _post(guide, email=f"v{number}@example.com", party_size="1", hour=17))
        booked = TourBooking.objects.count()
        content = _client().post("/", _post(guide, email="late@example.com", party_size="1")).content.decode()
        assert "data-tour-error>Too many bookings from this connection. Try again in an hour.</div>" in content
        assert TourBooking.objects.count() == booked
        assert not TourBooking.objects.filter(visitor_email="late@example.com").exists()

    def it_disables_the_book_button_once_pressed(guide: Member, hours: Any):
        content = _client().get(f"/?tour={_key(guide, 16)}").content.decode()
        assert 'data-tour-form data-pl-busy-submit data-pl-busy-label="Booking..."' in content
        assert "window.plBusySubmitBound" in content
        assert "No password. You sign in with a code we email you." in content
        assert "shows up in your account" not in content

    def it_refuses_a_tour_key_for_nobody(guide: Member, hours: Any):
        content = _client().post("/", _post(guide, tour="0-20300101T0000Z")).content.decode()
        assert "That time is no longer open, pick another." in content
        assert not TourBooking.objects.exists()

    def it_shows_the_form_again_with_its_errors(guide: Member, hours: Any):
        content = _client().post("/", _post(guide, email="not an email")).content.decode()
        assert "data-tour-form" in content
        assert "Enter a valid email address." in content
        assert '<option value="5">' not in content
        assert not TourBooking.objects.exists()

    def it_says_the_time_is_gone_for_a_bad_post_on_a_tour_no_longer_open(guide: Member, hours: Any):
        content = _client().post("/", _post(guide, tour="nonsense", email="")).content.decode()
        assert "That time is no longer open, pick another." in content


@pytest.fixture
def booking(guide: Member, hours: Any) -> TourBooking:
    return TourBookingFactory(guide=guide, starts_at=local(_day(), 16), party_size=2, visitor_email="sam@example.com")


def _url(name: str, booking: TourBooking) -> str:
    return reverse(f"tours_public:{name}", args=[booking.self_serve_token])


def describe_manage_page():
    def it_shows_the_booking_and_its_buttons(booking: TourBooking):
        content = _client().get(_url("manage", booking)).content.decode()
        assert "Your Tour</h1>" in content
        assert "2 people, with Gina</dd>" in content
        assert "data-tour-reschedule" in content and "data-tour-cancel" in content

    def it_lands_after_a_move_and_after_a_cancel(booking: TourBooking):
        assert "Your Tour Moved</h1>" in _client().get(f"{_url('manage', booking)}?moved=1").content.decode()
        booking.status = TourBooking.Status.CANCELLED
        booking.save()
        content = _client().get(f"{_url('manage', booking)}?cancelled=1").content.decode()
        assert "Your Tour Is Cancelled</h1>" in content
        assert "data-tour-cancelled>" in content
        assert "data-tour-book-again>Book a new tour</a>" in content
        assert "data-tour-reschedule" not in content

    def it_says_too_close_inside_the_cutoff(guide: Member, hours: Any):
        soon = TourBookingFactory(guide=guide, starts_at=timezone.now() + timedelta(hours=3))
        content = _client().get(_url("manage", soon)).content.decode()
        assert "data-tour-too-close>It is too close to your tour to change it online. Email us at" in content
        assert "data-tour-reschedule" not in content

    def it_thanks_a_visitor_whose_tour_has_passed(guide: Member, hours: Any):
        past = TourBookingFactory(guide=guide, starts_at=timezone.now() - timedelta(days=1))
        assert "This tour has already happened." in _client().get(_url("manage", past)).content.decode()

    def it_lands_on_a_friendly_page_for_a_tampered_token(booking: TourBooking):
        for name in ("manage", "reschedule", "cancel"):
            response = _client().get(reverse(f"tours_public:{name}", args=[booking.self_serve_token + "x"]))
            assert response.status_code == 404
            assert "We Could Not Find That Tour</h1>" in response.content.decode()
        assert _client().post(reverse("tours_public:cancel", args=["nope"])).status_code == 404
        booking.refresh_from_db()
        assert booking.status == TourBooking.Status.BOOKED


def describe_reschedule_page():
    def it_offers_every_other_time_with_room_for_the_party(guide: Member, booking: TourBooking):
        TourBookingFactory(guide=guide, starts_at=local(_day() + timedelta(days=7), 17), party_size=3)
        content = _client().get(f"{_url('reschedule', booking)}?date={_day():%Y-%m-%d}").content.decode()
        assert f'data-tour-time="{_key(guide, 17)}"' in content
        assert f'data-tour-time="{_key(guide, 16)}"' not in content

    def it_confirms_then_moves_on_post(guide: Member, booking: TourBooking):
        page = _client().get(f"{_url('reschedule', booking)}?tour={_key(guide, 17)}").content.decode()
        assert "Move your tour to" in page and "data-tour-move-form" in page
        booking.refresh_from_db()
        assert booking.starts_at == local(_day(), 16)
        response = _client().post(_url("reschedule", booking), {"tour": _key(guide, 17)})
        assert response["Location"] == f"{_url('manage', booking)}?moved=1"
        booking.refresh_from_db()
        assert (booking.starts_at, booking.sequence) == (local(_day(), 17), 1)

    def it_shows_why_a_move_failed(guide: Member, booking: TourBooking):
        TourBookingFactory(guide=guide, starts_at=local(_day(), 17), party_size=3)
        content = _client().post(_url("reschedule", booking), {"tour": _key(guide, 17)}).content.decode()
        assert "data-tour-error>Only 1 seat is left at that time.</div>" in content
        assert "data-tour-move-form" not in content

    def it_offers_no_time_and_moves_nothing_while_bookings_are_closed(guide: Member, booking: TourBooking):
        tour_settings = FacilityTourSettings.load()
        tour_settings.accepting_bookings = False
        tour_settings.closed_message = "Back in the spring."
        tour_settings.save()
        page = _client().get(_url("reschedule", booking)).content.decode()
        assert "data-tour-closed>Back in the spring.</p>" in page
        assert "data-tour-time" not in page
        assert f'href="{_url("cancel", booking)}"' in page
        refused = _client().post(_url("reschedule", booking), {"tour": _key(guide, 17)}).content.decode()
        assert "data-tour-closed>" in refused
        booking.refresh_from_db()
        assert (booking.starts_at, booking.sequence) == (local(_day(), 16), 0)
        assert _client().post(_url("cancel", booking)).status_code == 302
        booking.refresh_from_db()
        assert booking.status == TourBooking.Status.CANCELLED

    def it_refuses_a_key_for_nobody(booking: TourBooking):
        content = _client().post(_url("reschedule", booking), {"tour": "bad"}).content.decode()
        assert "That time is no longer open, pick another." in content

    def it_shows_the_too_close_page_and_refuses_a_forged_post(guide: Member, hours: Any):
        soon = TourBookingFactory(guide=guide, starts_at=timezone.now() + timedelta(hours=3))
        page = _client().get(_url("reschedule", soon))
        assert 'data-tour-reason="too_close"' in page.content.decode()
        forged = _client().post(_url("reschedule", soon), {"tour": _key(guide, 17)})
        assert 'data-tour-reason="too_close"' in forged.content.decode()
        before = soon.starts_at
        soon.refresh_from_db()
        assert (soon.starts_at, soon.sequence) == (before, 0)

    def it_refuses_a_cancelled_tour(booking: TourBooking):
        booking.status = TourBooking.Status.CANCELLED
        booking.save()
        content = _client().get(_url("reschedule", booking)).content.decode()
        assert 'data-tour-reason="cancelled"' in content

    def it_shows_the_refusal_when_the_booking_locks_between_page_and_post(guide: Member, booking: TourBooking):
        TourBooking.objects.filter(pk=booking.pk).update(status=TourBooking.Status.CANCELLED)
        with patch("tours.public_views._refusal", return_value=""):
            content = _client().post(_url("reschedule", booking), {"tour": _key(guide, 17)}).content.decode()
        assert 'data-tour-reason="cancelled"' in content


def describe_cancel_page():
    def it_changes_nothing_on_get(booking: TourBooking):
        content = _client().get(_url("cancel", booking)).content.decode()
        assert "Cancel Your Tour?</h1>" in content
        booking.refresh_from_db()
        assert booking.status == TourBooking.Status.BOOKED

    def it_cancels_on_post_and_frees_the_seats(guide: Member, booking: TourBooking):
        response = _client().post(_url("cancel", booking))
        assert response["Location"] == f"{_url('manage', booking)}?cancelled=1"
        booking.refresh_from_db()
        assert booking.status == TourBooking.Status.CANCELLED
        tour = open_tour_at(guide, local(_day(), 16))
        assert tour is not None and tour.seats_left == 4

    def it_shows_the_too_close_page_and_refuses_a_forged_post(guide: Member, hours: Any):
        soon = TourBookingFactory(guide=guide, starts_at=timezone.now() + timedelta(hours=3))
        assert 'data-tour-reason="too_close"' in _client().get(_url("cancel", soon)).content.decode()
        assert 'data-tour-reason="too_close"' in _client().post(_url("cancel", soon)).content.decode()
        soon.refresh_from_db()
        assert soon.status == TourBooking.Status.BOOKED

    def it_refuses_a_past_tour(guide: Member, hours: Any):
        past = TourBookingFactory(guide=guide, starts_at=timezone.now() - timedelta(days=1))
        assert 'data-tour-reason="past"' in _client().post(_url("cancel", past)).content.decode()

    def it_shows_the_refusal_when_the_booking_locks_between_page_and_post(booking: TourBooking):
        TourBooking.objects.filter(pk=booking.pk).update(status=TourBooking.Status.CANCELLED)
        with patch("tours.public_views._refusal", return_value=""):
            content = _client().post(_url("cancel", booking)).content.decode()
        assert 'data-tour-reason="cancelled"' in content


def describe_google_analytics():
    def it_redacts_the_booking_token_before_a_page_is_sent(booking: TourBooking):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-TOURS948"
        config.save()
        content = _client().get(_url("manage", booking)).content.decode()
        assert "var tourToken = /^\\/tour\\/b\\/[^\\/]+/;" in content
        assert "u.pathname.replace(tourToken, '/tour/b/redacted')" in content
