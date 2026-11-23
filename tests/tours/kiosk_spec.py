"""The front desk kiosk (#950): the kiosk key and cookie, finding today's tour, checking in, and booking."""

from __future__ import annotations

import re
from datetime import UTC, date, time, timedelta
from html import unescape
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client, RequestFactory
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escapejs

from core.models import EventDelivery, Notification, UserProfile
from membership.models import Member
from tests.membership.factories import MemberFactory
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours import kiosk
from tours.availability import OpenTour
from tours.booking import TourLockedError, check_in_tour
from tours.forms import TourBookingForm
from tours.models import FacilityTourSettings, TourBooking, TourGuideSettings
from tours.picker import build_picker, either_guide

pytestmark = pytest.mark.django_db

HOST = "tours.pastlives.space"
HOME = reverse("tours_kiosk:home")
CHECK_IN = reverse("tours_kiosk:check_in")
RESULT = reverse("tours_kiosk:check_in_result")
BOOK = reverse("tours_kiosk:book")
BOOKED = reverse("tours_kiosk:booked")


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    """Each spec starts with the check in and booking limits unspent (their counters live in the cache)."""
    cache.clear()


def _today() -> date:
    return timezone.localdate()


def _with_guide_user(member: Member) -> Member:
    """The member with a linked account, so their bell can ring."""
    signed_in(member)
    member.refresh_from_db()
    return member


@pytest.fixture
def key() -> str:
    return FacilityTourSettings.load().rotate_kiosk_key()


@pytest.fixture
def kiosk_client(key: str) -> Client:
    """A client on the tours host that opened the kiosk link, so it holds the kiosk cookie."""
    client = Client(HTTP_HOST=HOST)
    response = client.get(HOME, {"key": key})
    assert response.status_code == 302
    return client


def _today_booking(guide: Member, hour: int = 16, **kwargs: Any) -> TourBooking:
    return TourBookingFactory(guide=guide, starts_at=local(_today(), hour), **kwargs)


def describe_name_matches():
    @pytest.mark.parametrize(
        "typed",
        ["Sam Rivera", "  sam   RIVERA ", "sam", "Rivera", "Sam R", "sam r.", "Sam Riv", "Sam, Rivera"],
    )
    def it_forgives_case_spacing_and_partial_names(typed: str):
        assert kiosk.name_matches(typed, "Sam Rivera")

    @pytest.mark.parametrize("typed", ["", "   ", "Sa", "Sam Q", "Pat Rivera", "Sam Rivera Jones", "Sam Ann R"])
    def it_refuses_other_names(typed: str):
        assert not kiosk.name_matches(typed, "Sam Rivera")

    @pytest.mark.parametrize(
        ("typed", "booked"),
        [
            ("jose", "José Álvarez"),
            ("José Alvarez", "Jose Alvarez"),
            ("Jean-Luc", "Jean-Luc Picard"),
            ("jean luc p", "Jean-Luc Picard"),
            ("Picard Jean-Luc", "Jean-Luc Picard"),
            ("Mary Ann", "Mary Ann Smith"),
            ("Mary Smith", "Mary Ann Smith"),
            ("smith mary ann", "Mary Ann Smith"),
            ("Rivera Sam", "Sam Rivera"),
            ("R Sam", "Sam Rivera"),
            ("o'neil", "Pat O\u2019Neil"),
            ("pat o'neil", "Pat O'Neil"),
        ],
    )
    def it_folds_accents_splits_hyphens_and_reads_either_way(typed: str, booked: str):
        assert kiosk.name_matches(typed, booked)

    @pytest.mark.parametrize(("typed", "booked"), [("Ann", "Mary Ann Smith"), ("Luc", "Jean-Luc Picard")])
    def it_still_wants_a_first_or_last_name(typed: str, booked: str):
        assert not kiosk.name_matches(typed, booked)

    def it_matches_a_one_word_booking_by_that_word():
        assert kiosk.name_matches("cher", "Cher")
        assert not kiosk.name_matches("cher x", "Cher")

    def it_never_matches_a_blank_booking_name():
        assert not kiosk.name_matches("sam", "  ")


def describe_kiosk_key():
    def it_matches_only_the_current_key():
        rules = FacilityTourSettings.load()
        assert not kiosk.key_matches("", rules)
        first = rules.rotate_kiosk_key()
        assert kiosk.key_matches(first, rules)
        assert not kiosk.key_matches(first + "x", rules)
        second = rules.rotate_kiosk_key()
        assert second != first
        assert not kiosk.key_matches(first, rules)
        assert FacilityTourSettings.load().kiosk_key == second

    def it_fingerprints_the_key_never_carries_it():
        rules = FacilityTourSettings.load()
        assert rules.kiosk_fingerprint == ""
        key = rules.rotate_kiosk_key()
        assert rules.kiosk_fingerprint and key not in rules.kiosk_fingerprint

    def it_knows_the_kiosk_by_its_signed_cookie():
        rules = FacilityTourSettings.load()
        factory = RequestFactory()
        assert not kiosk.is_kiosk(factory.get("/kiosk/"), rules)
        rules.rotate_kiosk_key()
        response = HttpResponse()
        kiosk.remember_kiosk(factory.get("/kiosk/"), response, rules)
        cookie = response.cookies[kiosk.COOKIE_NAME]
        assert cookie["path"] == "/kiosk/" and cookie["httponly"] and cookie["samesite"] == "Lax"
        assert int(cookie["max-age"]) == kiosk.COOKIE_MAX_AGE
        request = factory.get("/kiosk/")
        request.COOKIES[kiosk.COOKIE_NAME] = cookie.value
        assert kiosk.is_kiosk(request, rules)
        forged = factory.get("/kiosk/")
        forged.COOKIES[kiosk.COOKIE_NAME] = rules.kiosk_fingerprint
        assert not kiosk.is_kiosk(forged, rules)


def describe_todays_tours():
    def it_lists_only_tours_still_booked_for_today(guide: Member):
        later = _today_booking(guide, 17, visitor_name="Bea Later")
        sooner = _today_booking(guide, 9, visitor_name="Ann Early")
        _today_booking(guide, 10, visitor_name="Cy Cancelled", status=TourBooking.Status.CANCELLED)
        _today_booking(guide, 11, visitor_name="Di Done", status=TourBooking.Status.ATTENDED)
        TourBookingFactory(guide=guide, starts_at=local(_today() + timedelta(days=1), 16), visitor_name="Ed Tomorrow")
        TourBookingFactory(guide=guide, starts_at=local(_today() - timedelta(days=1), 16), visitor_name="Fi Yesterday")
        assert list(kiosk.todays_tours(_today())) == [sooner, later]

    def it_finds_by_email_ignoring_case(guide: Member):
        booking = _today_booking(guide, visitor_email="Sam.Rivera@Example.com")
        _today_booking(guide, visitor_email="sam.rivera@example.com", status=TourBooking.Status.CANCELLED)
        assert kiosk.find_by_email("  SAM.rivera@example.COM ", _today()) == [booking]
        assert kiosk.find_by_email("   ", _today()) == []

    def it_finds_by_name_and_by_tapped_pk(guide: Member):
        sam = _today_booking(guide, 16, visitor_name="Sam Rivera")
        other = _today_booking(guide, 17, visitor_name="Sam Ortiz")
        assert kiosk.find_by_name("sam", _today()) == [sam, other]
        assert kiosk.find_by_name("sam r", _today()) == [sam]
        assert kiosk.find_by_pk(str(other.pk), _today()) == [other]
        assert kiosk.find_by_pk("abc", _today()) == []
        tomorrow = TourBookingFactory(guide=guide, starts_at=local(_today() + timedelta(days=1), 16))
        assert kiosk.find_by_pk(str(tomorrow.pk), _today()) == []

    def it_names_a_visitor_by_first_name_and_last_initial():
        assert TourBooking(visitor_name="sam  de la rivera").kiosk_name == "sam R."
        assert TourBooking(visitor_name=" Cher ").kiosk_name == "Cher"


def describe_check_in_tour():
    def it_stamps_the_check_in_and_pings_the_guide_once(guide: Member, django_capture_on_commit_callbacks: Any):
        guide = _with_guide_user(guide)
        booking = _today_booking(guide, 16, party_size=3, visitor_name="Sam Rivera")
        with django_capture_on_commit_callbacks(execute=True):
            checked, first = check_in_tour(booking)
        assert first and checked.checked_in_at is not None
        assert checked.status == TourBooking.Status.BOOKED
        with django_capture_on_commit_callbacks(execute=True):
            again, first_again = check_in_tour(booking)
        assert not first_again and again.checked_in_at == checked.checked_in_at
        bell = Notification.objects.get(user=guide.user, trigger="tour.checked_in")
        assert bell.title == "Your 4:00 tour is here"
        assert bell.body == "Sam Rivera, 3 people, checked in at the front desk."
        assert bell.url == reverse("tours:upcoming")
        assert EventDelivery.objects.filter(event_key="tour.checked_in", channel="in_app").count() == 1

    def it_pings_the_admins_for_a_tour_with_no_guide(django_capture_on_commit_callbacks: Any):
        admin = _with_guide_user(MemberFactory(fog_role=Member.FogRole.ADMIN))
        booking = TourBookingFactory(guide=None, starts_at=local(_today(), 16))
        with django_capture_on_commit_callbacks(execute=True):
            check_in_tour(booking)
        bell = Notification.objects.get(user=admin.user, trigger="tour.checked_in")
        assert bell.title == "A 4:00 tour is here (no guide)"

    def it_never_marks_attended_or_stamps_the_completed_tour(guide: Member):
        visitor = _with_guide_user(MemberFactory())
        booking = _today_booking(guide, user=visitor.user)
        check_in_tour(booking)
        booking.refresh_from_db()
        assert booking.status == TourBooking.Status.BOOKED
        assert not UserProfile.objects.filter(user=visitor.user, completed_tour_at__isnull=False).exists()

    @pytest.mark.parametrize(
        ("status", "days", "reason"),
        [
            (TourBooking.Status.CANCELLED, 0, "cancelled"),
            (TourBooking.Status.ATTENDED, 0, "past"),
            (TourBooking.Status.BOOKED, 1, "not_today"),
            (TourBooking.Status.BOOKED, -1, "not_today"),
        ],
    )
    def it_refuses_a_tour_that_is_not_todays_and_booked(guide: Member, status: str, days: int, reason: str):
        booking = TourBookingFactory(guide=guide, starts_at=local(_today() + timedelta(days=days), 16), status=status)
        with pytest.raises(TourLockedError) as refused:
            check_in_tour(booking)
        assert refused.value.reason == reason


def describe_kiosk_home():
    def it_shows_the_booking_page_without_the_kiosk_cookie():
        response = Client(HTTP_HOST=HOST).get(HOME)
        assert response.status_code == 302 and response["Location"] == "/"
        for path in (CHECK_IN, RESULT, BOOK, BOOKED):
            assert Client(HTTP_HOST=HOST).get(path)["Location"] == "/"

    def it_refuses_a_wrong_key_and_any_key_before_one_is_made():
        assert Client(HTTP_HOST=HOST).get(HOME, {"key": "guess"})["Location"] == "/"
        FacilityTourSettings.load().rotate_kiosk_key()
        client = Client(HTTP_HOST=HOST)
        response = client.get(HOME, {"key": "guess"})
        assert response["Location"] == "/" and kiosk.COOKIE_NAME not in response.cookies

    def it_sets_the_cookie_from_the_link_and_drops_the_key_from_the_address(key: str):
        client = Client(HTTP_HOST=HOST)
        response = client.get(HOME, {"key": key})
        assert response.status_code == 302 and response["Location"] == HOME
        assert response.cookies[kiosk.COOKIE_NAME].value
        home = client.get(HOME)
        assert home.status_code == 200
        content = home.content.decode()
        assert 'data-kiosk-screen="home"' in content
        assert f'href="{CHECK_IN}"' in content and f'href="{BOOK}"' in content
        assert kiosk.COOKIE_NAME in home.cookies  # renewed on every visit
        assert "no-store" in home["Cache-Control"]
        assert "cp-topbar__nav" not in content

    def it_locks_out_a_screen_holding_the_old_key_once_rotated(kiosk_client: Client):
        assert kiosk_client.get(HOME).status_code == 200
        FacilityTourSettings.load().rotate_kiosk_key()
        assert kiosk_client.get(HOME)["Location"] == "/"
        assert kiosk_client.get(CHECK_IN)["Location"] == "/"

    def it_is_not_found_on_any_other_host(key: str):
        assert Client().get(HOME, {"key": key}).status_code == 404


def describe_kiosk_check_in():
    def it_lists_todays_names_only_to_the_kiosk(kiosk_client: Client, guide: Member):
        _today_booking(guide, 16, visitor_name="Sam Rivera")
        _today_booking(guide, 17, visitor_name="Pat Gone", status=TourBooking.Status.CANCELLED)
        TourBookingFactory(guide=guide, starts_at=local(_today() + timedelta(days=1), 16), visitor_name="Tom Morrow")
        response = kiosk_client.get(CHECK_IN)
        content = response.content.decode()
        assert "Sam R." in content and "4:00 PM" in content
        assert "Pat G." not in content and "Tom M." not in content
        assert 'name="email"' in content and 'name="name"' in content
        assert 'autocomplete="off"' in content and "no-store" in response["Cache-Control"]
        assert "Sam R." not in Client(HTTP_HOST=HOST).get(CHECK_IN, follow=True).content.decode()

    def it_says_so_when_nobody_is_booked_today(kiosk_client: Client):
        assert "data-kiosk-no-names" in kiosk_client.get(CHECK_IN).content.decode()

    def it_checks_in_by_email_and_welcomes_once(
        kiosk_client: Client, guide: Member, django_capture_on_commit_callbacks: Any
    ):
        guide = _with_guide_user(guide)
        booking = _today_booking(guide, visitor_name="Sam Rivera", visitor_email="sam@example.com")
        with django_capture_on_commit_callbacks(execute=True):
            response = kiosk_client.post(CHECK_IN, {"email": "SAM@example.com"})
        assert response["Location"] == RESULT
        welcome = kiosk_client.get(RESULT).content.decode()
        assert "Welcome, Sam!" in welcome and "Gina will be right with you." in welcome
        assert 'data-kiosk-done="8"' in welcome
        booking.refresh_from_db()
        assert booking.checked_in_at is not None
        assert Notification.objects.filter(user=guide.user, trigger="tour.checked_in").count() == 1
        # Opened again (the back button, a reload): nothing to show, home.
        assert kiosk_client.get(RESULT)["Location"] == HOME

    def it_asks_is_this_you_before_a_tapped_name_checks_in(kiosk_client: Client, guide: Member):
        booking = _today_booking(guide, visitor_name="Sam Rivera")
        kiosk_client.post(CHECK_IN, {"pick": str(booking.pk)})
        confirm = kiosk_client.get(RESULT).content.decode()
        assert 'data-kiosk-screen="confirm"' in confirm and "Sam R., 4:00 PM" in confirm
        assert f'name="booking" value="{booking.pk}"' in confirm and "Yes, Check Me In" in confirm
        assert f'href="{CHECK_IN}" class="pl-kiosk__choice" data-kiosk-not-me' in confirm
        booking.refresh_from_db()
        assert booking.checked_in_at is None
        kiosk_client.post(CHECK_IN, {"booking": str(booking.pk)})
        assert "Welcome, Sam!" in kiosk_client.get(RESULT).content.decode()
        booking.refresh_from_db()
        assert booking.checked_in_at is not None

    def it_answers_a_tap_on_a_tour_no_longer_listed(kiosk_client: Client, guide: Member):
        booking = _today_booking(guide, status=TourBooking.Status.CANCELLED)
        kiosk_client.post(CHECK_IN, {"pick": str(booking.pk)})
        assert 'data-kiosk-screen="no-match"' in kiosk_client.get(RESULT).content.decode()
        listed = _today_booking(guide)
        kiosk_client.post(CHECK_IN, {"pick": str(listed.pk)})
        listed.delete()
        assert kiosk_client.get(RESULT)["Location"] == HOME

    def it_welcomes_without_a_guide_name_when_the_tour_has_none(kiosk_client: Client):
        TourBookingFactory(guide=None, starts_at=local(_today(), 16), visitor_name="Sam Rivera")
        kiosk_client.post(CHECK_IN, {"name": "sam rivera"})
        assert "Someone will be right with you." in kiosk_client.get(RESULT).content.decode()

    def it_offers_a_pick_list_when_a_typed_name_matches_several(kiosk_client: Client, guide: Member):
        sam = _today_booking(guide, 16, visitor_name="Sam Rivera")
        other = _today_booking(guide, 17, visitor_name="Sam Ortiz")
        kiosk_client.post(CHECK_IN, {"name": "  SAM "})
        pick = kiosk_client.get(RESULT).content.decode()
        assert 'data-kiosk-screen="pick"' in pick
        assert f'name="pick" value="{sam.pk}"' in pick and f'name="pick" value="{other.pk}"' in pick
        assert "Sam R." in pick and "Sam O." in pick and "under that name" in pick
        kiosk_client.post(CHECK_IN, {"pick": str(other.pk)})
        assert "Sam O., 5:00 PM" in kiosk_client.get(RESULT).content.decode()
        sam.refresh_from_db()
        other.refresh_from_db()
        assert other.checked_in_at is None
        kiosk_client.post(CHECK_IN, {"booking": str(other.pk)})
        assert "Welcome, Sam!" in kiosk_client.get(RESULT).content.decode()
        other.refresh_from_db()
        sam.refresh_from_db()
        assert other.checked_in_at is not None and sam.checked_in_at is None

    def it_says_for_that_email_when_an_email_matches_several(kiosk_client: Client, guide: Member):
        _today_booking(guide, 16, visitor_email="family@example.com")
        _today_booking(guide, 17, visitor_email="family@example.com")
        kiosk_client.post(CHECK_IN, {"email": "family@example.com"})
        pick = kiosk_client.get(RESULT).content.decode()
        assert "for that email" in pick and "under that name" not in pick

    def it_shows_try_again_when_nothing_today_matches(kiosk_client: Client, guide: Member):
        _today_booking(guide, visitor_name="Sam Rivera", status=TourBooking.Status.CANCELLED)
        TourBookingFactory(guide=guide, starts_at=local(_today() + timedelta(days=1), 16), visitor_name="Sam Rivera")
        for data in ({"name": "Sam Rivera"}, {"email": "nobody@example.com"}, {"name": ""}):
            kiosk_client.post(CHECK_IN, data)
            no_match = kiosk_client.get(RESULT).content.decode()
            assert "We Could Not Find a Tour Today" in no_match
            assert f'href="{CHECK_IN}"' in no_match and f'href="{BOOK}"' in no_match

    def it_shows_try_again_when_the_tour_changed_under_the_visitor(
        kiosk_client: Client, guide: Member, monkeypatch: pytest.MonkeyPatch
    ):
        booking = _today_booking(guide, visitor_name="Sam Rivera")

        def cancelled_meanwhile(found: TourBooking) -> tuple[TourBooking, bool]:
            TourBooking.objects.filter(pk=found.pk).update(status=TourBooking.Status.CANCELLED)
            return check_in_tour(found)

        monkeypatch.setattr("tours.kiosk_views.check_in_tour", cancelled_meanwhile)
        kiosk_client.post(CHECK_IN, {"booking": str(booking.pk)})
        assert 'data-kiosk-screen="no-match"' in kiosk_client.get(RESULT).content.decode()

    def it_stops_after_too_many_tries(kiosk_client: Client, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("tours.kiosk_views.CHECK_IN_HOURLY_LIMIT", 2)
        for _ in range(2):
            kiosk_client.post(CHECK_IN, {"name": "nobody"})
        kiosk_client.post(CHECK_IN, {"name": "nobody"})
        assert 'data-kiosk-screen="limited"' in kiosk_client.get(RESULT).content.decode()

    def it_goes_home_when_a_welcomed_booking_is_gone(kiosk_client: Client, guide: Member):
        booking = _today_booking(guide)
        kiosk_client.post(CHECK_IN, {"booking": str(booking.pk)})
        booking.delete()
        assert kiosk_client.get(RESULT)["Location"] == HOME


def _key_for(guide: Member, starts_at: Any) -> str:
    return f"{guide.pk}-{starts_at.astimezone(UTC):%Y%m%dT%H%MZ}"


def describe_kiosk_booking():
    @pytest.fixture
    def day() -> date:
        return _today() + timedelta(days=4)

    def it_shows_the_websites_times_in_kiosk_chrome(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        content = kiosk_client.get(BOOK, {"date": f"{day:%Y-%m-%d}"}).content.decode()
        assert 'data-kiosk-screen="book"' in content
        assert f'href="{BOOK}?tour={_key_for(guide, local(day, 16))}"' in content

    def it_offers_the_guide_dropdown_where_guides_share_a_time_and_books_at_once(
        kiosk_client: Client, guide: Member, day: date
    ):
        other = make_guide(full_legal_name="Olive Other")
        TourGuideSettings.objects.create(guide=other, confirm_mode=TourGuideSettings.ConfirmMode.FIRST)
        for member in (guide, other):
            TourHoursFactory(guide=member, weekday=day.weekday(), seats=4)
        anyone = f"any-{local(day, 16).astimezone(UTC):%Y%m%dT%H%MZ}"
        times = kiosk_client.get(BOOK, {"date": f"{day:%Y-%m-%d}"}).content.decode()
        assert f'href="{BOOK}?tour={anyone}"' in times and times.count("data-tour-time=") == 2
        form = kiosk_client.get(BOOK, {"tour": anyone}).content.decode()
        assert "data-tour-guide" in form and "Olive (4 seats left)" in form
        data = {"tour": _key_for(other, local(day, 16)), "name": "Kim Kiosk", "email": "kim@example.com"}
        assert kiosk_client.post(BOOK, {**data, "phone": "503 555 0142", "party_size": "2"})["Location"] == BOOKED
        booking = TourBooking.objects.get()
        assert (booking.guide, booking.status) == (other, TourBooking.Status.BOOKED)

    def it_follows_the_websites_minimum_notice(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        tour_key = _key_for(guide, local(day, 16))
        assert "data-tour-form" in kiosk_client.get(BOOK, {"tour": tour_key}).content.decode()
        rules = FacilityTourSettings.load()
        rules.min_notice_hours = 24 * 6
        rules.save()
        content = kiosk_client.get(BOOK, {"tour": tour_key}).content.decode()
        assert "data-tour-form" not in content and tour_key not in content

    def it_books_with_kiosk_as_the_source_and_shows_it_once(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        tour_key = _key_for(guide, local(day, 16))
        form = kiosk_client.get(BOOK, {"tour": tour_key}).content.decode()
        assert "data-tour-form" in form and form.count('autocomplete="off"') >= 4
        assert 'name="create_account"' not in form
        response = kiosk_client.post(
            BOOK,
            {
                "tour": tour_key,
                "name": "Kim Kiosk",
                "email": "kim@example.com",
                "phone": "503 555 0142",
                "party_size": "2",
            },
        )
        assert response["Location"] == BOOKED
        booking = TourBooking.objects.get()
        assert booking.source == TourBooking.Source.KIOSK and booking.party_size == 2
        confirmation = kiosk_client.get(BOOKED).content.decode()
        assert "You're Booked, Kim!" in confirmation and 'data-kiosk-done="8"' in confirmation
        assert booking.self_serve_token not in confirmation
        assert kiosk_client.get(BOOKED)["Location"] == HOME

    def it_spends_its_own_booking_limit_never_the_websites(
        kiosk_client: Client, guide: Member, day: date, monkeypatch: pytest.MonkeyPatch
    ):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {
            "tour": _key_for(guide, local(day, 16)),
            "name": "Kim K",
            "email": "k@example.com",
            "phone": "503 555 0142",
            "party_size": "1",
        }
        # The website's allowance is spent (every phone on the makerspace Wi-Fi): the kiosk still books.
        monkeypatch.setattr("tours.public_views.BOOKING_HOURLY_LIMIT", 0)
        assert kiosk_client.post(BOOK, data)["Location"] == BOOKED
        assert TourBooking.objects.get().source == TourBooking.Source.KIOSK
        # Its own allowance spent, the kiosk says so.
        monkeypatch.setattr("tours.public_views.KIOSK_BOOKING_HOURLY_LIMIT", 0)
        data["email"] = "other@example.com"
        assert "Too many bookings" in kiosk_client.post(BOOK, data).content.decode()
        assert TourBooking.objects.count() == 1

    def it_never_links_the_booking_to_whoever_is_signed_in(guide: Member, key: str, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        client = signed_in(guide)
        client.defaults["HTTP_HOST"] = HOST
        client.get(HOME, {"key": key})
        client.post(
            BOOK,
            {
                "tour": _key_for(guide, local(day, 16)),
                "name": "Kim Kiosk",
                "email": "kim@example.com",
                "phone": "503 555 0142",
                "party_size": "1",
            },
        )
        assert TourBooking.objects.get().user is None

    def it_says_already_booked_on_a_second_press(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {
            "tour": _key_for(guide, local(day, 16)),
            "name": "Kim K",
            "email": "k@example.com",
            "phone": "503 555 0142",
            "party_size": "1",
        }
        kiosk_client.post(BOOK, data)
        kiosk_client.get(BOOKED)
        assert kiosk_client.post(BOOK, data)["Location"] == BOOKED
        assert "data-kiosk-already" in kiosk_client.get(BOOKED).content.decode()
        assert TourBooking.objects.count() == 1

    def it_shows_the_times_again_with_why_when_the_time_is_gone(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {
            "tour": f"{guide.pk}-20000101T0000Z",
            "name": "Kim K",
            "email": "k@example.com",
            "phone": "503 555 0142",
            "party_size": "1",
        }
        content = kiosk_client.post(BOOK, data).content.decode()
        assert "data-tour-error" in content and 'data-kiosk-screen="book"' in content

    def it_shows_the_form_again_with_what_to_fix(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {"tour": _key_for(guide, local(day, 16)), "name": "Kim K", "email": "not an email", "party_size": "1"}
        content = kiosk_client.post(BOOK, data).content.decode()
        assert "data-tour-form" in content and "reg-errors" in content
        assert not TourBooking.objects.exists()

    def it_sends_a_filled_honeypot_back_to_the_page(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {
            "tour": _key_for(guide, local(day, 16)),
            "name": "Bot",
            "email": "bot@example.com",
            "party_size": "1",
            "website": "spam",
        }
        assert kiosk_client.post(BOOK, data)["Location"] == BOOK
        assert not TourBooking.objects.exists()

    def it_shows_the_closed_message_while_bookings_are_closed(kiosk_client: Client):
        rules = FacilityTourSettings.load()
        rules.accepting_bookings = False
        rules.closed_message = "Back in spring."
        rules.save()
        assert "Back in spring." in kiosk_client.get(BOOK).content.decode()

    def it_goes_home_when_the_booked_tour_is_gone(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {
            "tour": _key_for(guide, local(day, 16)),
            "name": "Kim K",
            "email": "k@example.com",
            "phone": "503 555 0142",
            "party_size": "1",
        }
        kiosk_client.post(BOOK, data)
        TourBooking.objects.all().delete()
        assert kiosk_client.get(BOOKED)["Location"] == HOME

    def it_keeps_the_websites_autocomplete_off_the_kiosk():
        website = TourBookingForm(max_party=2)
        at_kiosk = TourBookingForm(max_party=2, kiosk=True)
        assert website.fields["email"].widget.attrs["autocomplete"] == "email"
        assert {at_kiosk.fields[name].widget.attrs["autocomplete"] for name in ("name", "email", "phone")} == {"off"}


def describe_kiosk_settings():
    SETTINGS = reverse("tours:settings")
    KIOSK_KEY = reverse("tours:kiosk_key")

    def it_creates_then_rotates_the_kiosk_link(admin: Member, settings: Any):
        settings.TOURS_BASE_URL = "https://tours.example.test"
        client = signed_in(admin)
        before = client.get(SETTINGS).content.decode()
        assert "data-tour-kiosk-create" in before and "data-tour-kiosk-url" not in before
        client.post(KIOSK_KEY)
        first = FacilityTourSettings.load().kiosk_key
        page = client.get(SETTINGS).content.decode()
        assert f"https://tours.example.test{HOME}?key={first}" in page and "data-tour-kiosk-rotate" in page
        client.post(KIOSK_KEY)
        assert FacilityTourSettings.load().kiosk_key not in ("", first)

    def it_is_for_admins_only(guide: Member):
        response = signed_in(guide).post(KIOSK_KEY)
        assert response.status_code in (302, 403)
        assert FacilityTourSettings.load().kiosk_key == ""

    def it_shows_the_check_in_on_the_staff_row(admin: Member, guide: Member):
        booking = TourBookingFactory(guide=guide, starts_at=timezone.now() + timedelta(hours=3))
        TourBooking.objects.filter(pk=booking.pk).update(checked_in_at=timezone.now())
        content = signed_in(admin).get(reverse("tours:upcoming")).content.decode()
        assert "data-tour-booking-checked-in" in content


def _tour(name: str, hour: int = 16) -> OpenTour:
    """An unsaved tour by a guide called ``name``, for the picker's naming alone."""
    starts_at = local(_today() + timedelta(days=4), hour)
    return OpenTour(
        guide=Member(full_legal_name=name),
        hours_id=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        seats=4,
        seats_taken=0,
    )


def describe_kiosk_guide_names():
    """#1273: the kiosk names who gives each tour, by first name, everywhere a visitor picks or books one."""

    @pytest.fixture
    def day() -> date:
        return _today() + timedelta(days=4)

    @pytest.mark.parametrize(
        ("names", "said"),
        [
            ([], ""),
            (["Amber Ash"], "Amber"),
            (["Amber Ash", "Sam Stone"], "Amber or Sam"),
            (["Amber Ash", "Sam Stone", "Lee Lark"], "Amber, Sam or Lee"),
            (["Amber Ash", "  ", "Sam Stone"], "Amber or Sam"),
            (["  ", "Sam Stone"], "Sam"),
        ],
    )
    def it_lists_the_guides_by_first_name(names: list[str], said: str):
        assert either_guide([_tour(name) for name in names]) == said

    def it_names_every_time_only_when_asked():
        alone = _tour("Amber Ash", 10)
        shared = [_tour("Amber Ash", 16), _tour("Sam Stone", 16)]
        named = build_picker([alone, *shared], "", no_preference=True, name_guides=True).times
        assert [(time.guide_name, time.no_preference) for time in named] == [("Amber", False), ("Amber or Sam", True)]
        website = build_picker([alone, *shared], "", no_preference=True).times
        assert [time.guide_name for time in website] == ["", ""]
        reschedule = build_picker([alone, *shared], "").times
        assert [time.guide_name for time in reschedule] == ["", "Amber", "Sam"]

    def it_says_with_whom_beside_each_kiosk_time(kiosk_client: Client, guide: Member, day: date):
        other = make_guide(full_legal_name="Olive Other")
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        TourHoursFactory(guide=other, weekday=day.weekday(), seats=4, start_time=time(17), end_time=time(18))
        times = kiosk_client.get(BOOK, {"date": f"{day:%Y-%m-%d}"}).content.decode()
        alone, shared = times.split('data-tour-time="')[1:3]
        assert "4:00 PM" in alone and "4 seats left with Gina</span>" in alone
        assert "data-tour-no-preference" in shared and "5:00 PM" in shared
        assert "4 seats left with Gina or Olive</span>" in shared

    def it_keeps_the_website_times_unnamed(guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        website = Client(HTTP_HOST=HOST).get("/", {"date": f"{day:%Y-%m-%d}"}).content.decode()
        assert "data-tour-time=" in website and "with Gina" not in website

    def it_names_the_one_guide_in_the_form_summary(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        form = kiosk_client.get(BOOK, {"tour": _key_for(guide, local(day, 16))}).content.decode()
        assert "<span data-tour-guide-name>with Gina</span>" in form
        assert "data-tour-change" not in form and "Pick a different time" not in form

    def it_names_no_guide_for_a_tour_whose_guide_has_no_name(kiosk_client: Client, day: date):
        nameless = make_guide(full_legal_name="", preferred_name="")
        TourHoursFactory(guide=nameless, weekday=day.weekday(), seats=4)
        form = kiosk_client.get(BOOK, {"tour": _key_for(nameless, local(day, 16))}).content.decode()
        assert "data-tour-form" in form and "data-tour-guide-name" not in form

    def it_follows_the_guide_dropdown_in_the_form_summary(kiosk_client: Client, guide: Member, day: date):
        other = make_guide(full_legal_name="Olive Other")
        for member in (guide, other):
            TourHoursFactory(guide=member, weekday=day.weekday(), seats=4)
        anyone = f"any-{local(day, 16).astimezone(UTC):%Y%m%dT%H%MZ}"
        olive = _key_for(other, local(day, 16))
        form = kiosk_client.get(BOOK, {"tour": anyone}).content.decode()
        assert 'data-tour-guide-name x-text="names[guide]">No preference</span>' in form
        assert f"'{escapejs(anyone)}': 'No preference'" in form and f"'{escapejs(olive)}': 'with Olive'" in form
        # Sent back to fix the email, the summary keeps the guide picked.
        data = {"tour": olive, "name": "Kim K", "email": "not an email", "party_size": "1"}
        again = kiosk_client.post(BOOK, data).content.decode()
        assert 'x-text="names[guide]">with Olive</span>' in again

    def it_keeps_a_way_back_to_the_times_on_the_website(guide: Member, day: date):
        other = make_guide(full_legal_name="Olive Other")
        for member in (guide, other):
            TourHoursFactory(guide=member, weekday=day.weekday(), seats=4)
        website = Client(HTTP_HOST=HOST)
        for tour in (_key_for(guide, local(day, 16)), f"any-{local(day, 16).astimezone(UTC):%Y%m%dT%H%MZ}"):
            form = website.get("/", {"tour": tour}).content.decode()
            # Morlock, 2026-11-22: the small Pick a different time link became "← Back to day & time".
            assert "data-tour-form" in form and "data-tour-back" in form and "Back to day &amp; time" in form
            assert "data-tour-guide-name" not in form and "names:" not in form

    def it_names_the_guide_on_you_are_booked(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {
            "tour": _key_for(guide, local(day, 16)),
            "name": "Kim K",
            "email": "k@example.com",
            "phone": "503 555 0142",
            "party_size": "1",
        }
        kiosk_client.post(BOOK, data)
        booked = kiosk_client.get(BOOKED).content.decode()
        assert '<p class="pl-kiosk__lead" data-kiosk-booked-guide>Your guide is Gina.</p>' in booked

    def it_names_no_guide_on_you_are_booked_when_the_tour_lost_its_guide(
        kiosk_client: Client, guide: Member, day: date
    ):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {
            "tour": _key_for(guide, local(day, 16)),
            "name": "Kim K",
            "email": "k@example.com",
            "phone": "503 555 0142",
            "party_size": "1",
        }
        kiosk_client.post(BOOK, data)
        TourBooking.objects.update(guide=None)
        booked = kiosk_client.get(BOOKED).content.decode()
        assert "You're Booked, Kim!" in booked and "data-kiosk-booked-guide" not in booked

    def it_names_each_tours_guide_on_the_pick_list_only(kiosk_client: Client, guide: Member):
        other = make_guide(full_legal_name="Olive Other")
        _today_booking(guide, 16, visitor_name="Sam Rivera")
        _today_booking(other, 17, visitor_name="Sam Ortiz")
        TourBookingFactory(guide=None, starts_at=local(_today(), 18), visitor_name="Sam Nobody")
        kiosk_client.post(CHECK_IN, {"name": "sam"})
        pick = kiosk_client.get(RESULT).content.decode()
        assert "4:00 PM with Gina</span>" in pick and "5:00 PM with Olive</span>" in pick
        assert '<span class="pl-kiosk__name-at">6:00 PM</span>' in pick
        listed = kiosk_client.get(CHECK_IN).content.decode()
        assert '<span class="pl-kiosk__name-at">4:00 PM</span>' in listed and "with Gina" not in listed

    def it_names_the_guide_on_is_this_you(kiosk_client: Client, guide: Member):
        booking = _today_booking(guide, visitor_name="Sam Rivera")
        kiosk_client.post(CHECK_IN, {"pick": str(booking.pk)})
        confirm = kiosk_client.get(RESULT).content.decode()
        assert "data-kiosk-confirm-guide>Your guide is Gina.</p>" in confirm
        nobody = TourBookingFactory(guide=None, starts_at=local(_today(), 17), visitor_name="Pat Lee")
        kiosk_client.post(CHECK_IN, {"pick": str(nobody.pk)})
        assert "data-kiosk-confirm-guide" not in kiosk_client.get(RESULT).content.decode()

    def it_gives_the_bookings_guide_by_first_name_or_nothing(guide: Member):
        assert _today_booking(guide).guide_first_name == "Gina"
        assert TourBookingFactory(guide=None, starts_at=local(_today(), 17)).guide_first_name == ""


def _back(content: str) -> str | None:
    """Where the heading's Back goes, or None with no Back."""
    found = re.search(
        r'<a href="([^"]*)" class="pl-kiosk__back pl-kiosk__back--step" data-kiosk-step-back>Back</a>', content
    )
    return unescape(found.group(1)) if found else None


def describe_kiosk_back():
    """#1273: every step of the flow has Back, a real link one step back; home and the finished screens have none."""

    @pytest.fixture
    def day() -> date:
        return _today() + timedelta(days=4)

    def it_shows_no_back_at_home(kiosk_client: Client):
        home = kiosk_client.get(HOME).content.decode()
        assert _back(home) is None and "data-kiosk-step-back" not in home

    def it_goes_home_from_check_in(kiosk_client: Client):
        content = kiosk_client.get(CHECK_IN).content.decode()
        assert _back(content) == HOME
        assert 'data-kiosk-arrows>\n    <a href="' in content and "data-kiosk-back>Start Over</a>" in content

    def it_goes_home_from_the_times(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        assert _back(kiosk_client.get(BOOK, {"date": f"{day:%Y-%m-%d}"}).content.decode()) == HOME

    def it_goes_home_from_the_closed_message(kiosk_client: Client):
        rules = FacilityTourSettings.load()
        rules.accepting_bookings = False
        rules.save()
        assert _back(kiosk_client.get(BOOK).content.decode()) == HOME

    def it_goes_from_the_form_to_that_days_times(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        tour_key = _key_for(guide, local(day, 16))
        assert _back(kiosk_client.get(BOOK, {"tour": tour_key}).content.decode()) == f"{BOOK}?date={day:%Y-%m-%d}"
        data = {"tour": tour_key, "name": "Kim K", "email": "not an email", "party_size": "1"}
        assert _back(kiosk_client.post(BOOK, data).content.decode()) == f"{BOOK}?date={day:%Y-%m-%d}"

    def it_goes_home_from_the_times_shown_again_after_a_gone_time(kiosk_client: Client, guide: Member, day: date):
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {
            "tour": f"{guide.pk}-20000101T0000Z",
            "name": "Kim K",
            "email": "k@example.com",
            "phone": "503 555 0142",
            "party_size": "1",
        }
        assert _back(kiosk_client.post(BOOK, data).content.decode()) == HOME

    def it_goes_to_check_in_from_is_this_you_the_pick_list_and_no_match(kiosk_client: Client, guide: Member):
        booking = _today_booking(guide, 16, visitor_name="Sam Rivera")
        _today_booking(guide, 17, visitor_name="Sam Ortiz")
        kiosk_client.post(CHECK_IN, {"pick": str(booking.pk)})
        assert _back(kiosk_client.get(RESULT).content.decode()) == CHECK_IN
        kiosk_client.post(CHECK_IN, {"name": "sam"})
        assert _back(kiosk_client.get(RESULT).content.decode()) == CHECK_IN
        kiosk_client.post(CHECK_IN, {"name": "nobody at all"})
        no_match = kiosk_client.get(RESULT).content.decode()
        assert 'data-kiosk-screen="no-match"' in no_match and _back(no_match) == CHECK_IN

    def it_goes_to_check_in_from_too_many_tries(kiosk_client: Client, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("tours.kiosk_views.CHECK_IN_HOURLY_LIMIT", 0)
        kiosk_client.post(CHECK_IN, {"name": "nobody"})
        limited = kiosk_client.get(RESULT).content.decode()
        assert 'data-kiosk-screen="limited"' in limited and _back(limited) == CHECK_IN

    def it_leaves_the_finished_screens_to_done(kiosk_client: Client, guide: Member, day: date):
        booking = _today_booking(guide, visitor_name="Sam Rivera")
        kiosk_client.post(CHECK_IN, {"booking": str(booking.pk)})
        assert _back(kiosk_client.get(RESULT).content.decode()) is None
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        data = {
            "tour": _key_for(guide, local(day, 16)),
            "name": "Kim K",
            "email": "k@example.com",
            "phone": "503 555 0142",
            "party_size": "1",
        }
        kiosk_client.post(BOOK, data)
        assert _back(kiosk_client.get(BOOKED).content.decode()) is None

    def it_never_brings_back_a_past_result(kiosk_client: Client, guide: Member):
        booking = _today_booking(guide, visitor_name="Sam Rivera")
        kiosk_client.post(CHECK_IN, {"pick": str(booking.pk)})
        back = _back(kiosk_client.get(RESULT).content.decode())
        assert back == CHECK_IN
        again = kiosk_client.get(back).content.decode()
        assert 'data-kiosk-screen="check-in"' in again and "Is This You?" not in again
