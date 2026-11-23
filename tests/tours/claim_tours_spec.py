"""Tours find their visitor's account by email (Morlock, 2026-11-22), in place of the booking form's
"Create a free Past Lives account" switch: whoever later signs in or signs up with a verified email gets
the tours booked under it, however long after, and no account is ever made at booking."""

from __future__ import annotations

from typing import Any

import pytest
from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed, user_logged_in, user_signed_up
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, RequestFactory

from membership.models import Member
from tests.tours.factories import TourBookingFactory
from tours.booking import claim_tours
from tours.forms import TourBookingForm

pytestmark = pytest.mark.django_db


def _user(email: str, *, verified: bool = True) -> User:
    user = User.objects.create_user(email, email=email)
    # Creating a User provisions its EmailAddress already; set it to the state the spec needs.
    EmailAddress.objects.filter(user=user).delete()
    EmailAddress.objects.create(user=user, email=email, verified=verified, primary=True)
    return user


def describe_claim_tours():
    def it_links_unclaimed_tours_under_a_verified_email(guide: Member):
        booking = TourBookingFactory(guide=guide, visitor_email="Sam@Example.com", user=None)
        user = _user("sam@example.com")
        assert claim_tours(user) == 1
        booking.refresh_from_db()
        assert booking.user == user

    def it_ignores_an_email_not_yet_verified(guide: Member):
        booking = TourBookingFactory(guide=guide, visitor_email="sam@example.com", user=None)
        assert claim_tours(_user("sam@example.com", verified=False)) == 0
        booking.refresh_from_db()
        assert booking.user is None

    def it_never_takes_a_tour_linked_to_someone_else(guide: Member):
        owner = User.objects.create_user("owner", email="owner@example.com")
        booking = TourBookingFactory(guide=guide, visitor_email="sam@example.com", user=owner)
        assert claim_tours(_user("sam@example.com")) == 0
        booking.refresh_from_db()
        assert booking.user == owner

    def it_covers_every_verified_email_on_the_account(guide: Member):
        user = _user("sam@example.com")
        EmailAddress.objects.create(user=user, email="sam@work.example", verified=True, primary=False)
        TourBookingFactory(guide=guide, visitor_email="sam@work.example", user=None)
        TourBookingFactory(guide=guide, visitor_email="someone@else.example", user=None)
        assert claim_tours(user) == 1


def describe_when_it_runs():
    @pytest.fixture
    def request_() -> Any:
        return RequestFactory().get("/")

    def it_runs_at_sign_in(guide: Member, request_: Any):
        booking = TourBookingFactory(guide=guide, visitor_email="sam@example.com", user=None)
        user = _user("sam@example.com")
        user_logged_in.send(sender=User, request=request_, user=user)
        booking.refresh_from_db()
        assert booking.user == user

    def it_runs_at_signup(guide: Member, request_: Any):
        booking = TourBookingFactory(guide=guide, visitor_email="sam@example.com", user=None)
        user = _user("sam@example.com")
        user_signed_up.send(sender=User, request=request_, user=user)
        booking.refresh_from_db()
        assert booking.user == user

    def it_runs_when_an_email_is_confirmed(guide: Member, request_: Any):
        booking = TourBookingFactory(guide=guide, visitor_email="sam@example.com", user=None)
        user = _user("sam@example.com")
        address = EmailAddress.objects.get(user=user)
        email_confirmed.send(sender=EmailAddress, request=request_, email_address=address)
        booking.refresh_from_db()
        assert booking.user == user

    def it_never_stops_a_sign_in(guide: Member, request_: Any, monkeypatch: Any):
        def boom(user: User) -> int:
            raise RuntimeError("database hiccup")

        monkeypatch.setattr("tours.booking.claim_tours", boom)
        user_logged_in.send(sender=User, request=request_, user=_user("sam@example.com"))


def describe_the_booking_form():
    @pytest.fixture(autouse=True)
    def _fresh_rate_limits() -> None:
        cache.clear()

    def it_offers_no_account():
        form = TourBookingForm(max_party=2)
        assert "create_account" not in form.fields

    def it_makes_no_account_at_booking(guide: Member):
        from datetime import UTC, timedelta

        from django.utils import timezone

        from tests.tours.factories import TourHoursFactory, local
        from tours.models import TourBooking

        day = timezone.localdate() + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        key = f"{guide.pk}-{local(day, 16).astimezone(UTC):%Y%m%dT%H%MZ}"
        users = User.objects.count()
        data = {"tour": key, "name": "Sam Rivera", "email": "fresh@example.com", "phone": "503 555 0142"}
        data.update({"party_size": "1", "create_account": "on"})
        assert Client(HTTP_HOST="tours.pastlives.space").post("/", data).status_code == 302
        assert TourBooking.objects.get().user is None
        assert User.objects.count() == users


def describe_the_receivers_with_nothing_to_claim():
    def it_ignores_a_sign_in_without_a_user():
        from tours import claims

        claims.on_sign_in(sender=User, request=None, user=None)

    def it_ignores_a_confirmation_without_an_address():
        from tours import claims

        claims.on_email_confirmed(sender=EmailAddress, request=None, email_address=None)
