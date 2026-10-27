"""Booking, moving and cancelling a facility tour (#948): seats, accounts, refusals, and what each one sends."""

from __future__ import annotations

import threading
import time as clock
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail
from django.db import connection

from core.models import Notification, SiteConfiguration
from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours import booking as booking_service
from tours import signals
from tours.availability import open_tour_at
from tours.booking import (
    FILLED_MESSAGE,
    GONE_MESSAGE,
    SAME_TIME_MESSAGE,
    TourFullError,
    TourLockedError,
    TourUnavailableError,
    Visitor,
    book_tour,
    cancel_tour_by_visitor,
    reschedule_tour,
)
from tours.models import FacilityTourSettings, TourBooking

pytestmark = pytest.mark.django_db

# 2030-01-07 is a Monday; tours run Tuesday the 8th, 4:00 and 5:00 PM.
NOW = local(date(2030, 1, 7), 9)
TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)
FIVE = local(TUESDAY, 17)


def _ics(message: Any) -> str:
    """The calendar invite attached to an email, as text."""
    content = message.attachments[0][1]
    return content.decode() if isinstance(content, bytes) else str(content)


def _visitor(party: int = 2, email: str = "sam@example.com", name: str = "Sam Rivera") -> Visitor:
    return Visitor(name=name, email=email, phone="", party_size=party)


@pytest.fixture
def hours(guide: Member) -> Any:
    return TourHoursFactory(guide=guide, seats=4)


def describe_book_tour():
    def it_books_the_party_and_takes_its_seats(guide: Member, hours: Any):
        booking = book_tour(guide=guide, starts_at=FOUR, visitor=_visitor(2), now=NOW)
        assert (booking.guide, booking.starts_at, booking.ends_at, booking.party_size) == (
            guide,
            FOUR,
            FOUR + timedelta(hours=1),
            2,
        )
        assert (booking.visitor_name, booking.visitor_email, booking.status, booking.user) == (
            "Sam Rivera",
            "sam@example.com",
            TourBooking.Status.BOOKED,
            None,
        )
        tour = open_tour_at(guide, FOUR, now=NOW)
        assert tour is not None and tour.seats_left == 2

    def it_refuses_a_party_bigger_than_the_seats_left(guide: Member, hours: Any):
        TourBookingFactory(guide=guide, starts_at=FOUR, party_size=3)
        with pytest.raises(TourFullError, match=FILLED_MESSAGE) as refused:
            book_tour(guide=guide, starts_at=FOUR, visitor=_visitor(2), now=NOW)
        assert refused.value.seats_left == 1
        assert TourBooking.objects.count() == 1

    def it_refuses_a_time_no_hours_yield(guide: Member, hours: Any):
        with pytest.raises(TourUnavailableError, match=GONE_MESSAGE):
            book_tour(guide=guide, starts_at=local(TUESDAY, 16, 30), visitor=_visitor(), now=NOW)

    def it_links_a_signed_in_visitor(guide: Member, hours: Any):
        member = make_guide(full_legal_name="Lee Member")
        signed_in(member)
        member.refresh_from_db()
        booking = book_tour(
            guide=guide, starts_at=FOUR, visitor=_visitor(email="other@example.com"), user=member.user, now=NOW
        )
        assert booking.user == member.user

    def it_links_the_account_an_email_already_belongs_to_and_creates_nothing(guide: Member, hours: Any):
        user = User.objects.create_user("known", email="known@example.com")
        users = User.objects.count()
        booking = book_tour(
            guide=guide, starts_at=FOUR, visitor=_visitor(email="Known@Example.com"), create_account=True, now=NOW
        )
        assert booking.user == user
        assert User.objects.count() == users

    def it_creates_nothing_with_the_account_toggle_off(guide: Member, hours: Any):
        users = User.objects.count()
        booking = book_tour(guide=guide, starts_at=FOUR, visitor=_visitor(email="new@example.com"), now=NOW)
        assert booking.user is None
        assert User.objects.count() == users

    def it_makes_a_guest_account_with_the_toggle_on(guide: Member, hours: Any):
        booking = book_tour(
            guide=guide,
            starts_at=FOUR,
            visitor=_visitor(email="fresh@example.com", name="Sam Q Rivera"),
            create_account=True,
            now=NOW,
        )
        user = User.objects.get(email="fresh@example.com")
        assert booking.user == user
        assert TourBooking.objects.get(pk=booking.pk).user == user
        assert (user.first_name, user.last_name) == ("Sam", "Q Rivera")
        assert Member.objects.get(user=user).status == Member.Status.GUEST
        assert EmailAddress.objects.filter(user=user, email="fresh@example.com").exists()

    def it_makes_a_guest_account_under_invite_only_too(guide: Member, hours: Any):
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
        config.save()
        booking = book_tour(
            guide=guide, starts_at=FOUR, visitor=_visitor(email="fresh@example.com"), create_account=True, now=NOW
        )
        assert booking.user is not None
        assert Member.objects.get(user=booking.user).status == Member.Status.GUEST

    def it_keeps_the_booking_when_the_account_cannot_be_made(guide: Member, hours: Any):
        with patch("core.services.guest_account._find_or_create_user", side_effect=RuntimeError("boom")):
            booking = book_tour(
                guide=guide, starts_at=FOUR, visitor=_visitor(email="fresh@example.com"), create_account=True, now=NOW
            )
        assert TourBooking.objects.get(pk=booking.pk).user is None


def describe_what_booking_sends():
    def it_emails_the_visitor_with_an_invite_and_tells_the_guide(
        guide: Member, hours: Any, django_capture_on_commit_callbacks: Any
    ):
        signed_in(guide)
        guide.refresh_from_db()
        with django_capture_on_commit_callbacks(execute=True):
            booking = book_tour(guide=guide, starts_at=FOUR, visitor=_visitor(2), now=NOW)
        visitor_mail = [message for message in mail.outbox if message.to == ["sam@example.com"]]
        assert len(visitor_mail) == 1
        assert visitor_mail[0].subject == "Your Past Lives tour is booked: Tue, Jan 8 at 4:00 PM"
        assert visitor_mail[0].attachments[0][0] == "tour.ics"
        invite = _ics(visitor_mail[0])
        assert f"UID:tour-{booking.pk}@pastlives" in invite
        assert "SEQUENCE:0" in invite
        assert f"/tour/b/{booking.self_serve_token}/reschedule/" in visitor_mail[0].body
        guide_mail = [message for message in mail.outbox if message.to == [guide.user.email]]
        assert [message.subject for message in guide_mail] == ["New tour booked: Tue, Jan 8 at 4:00 PM"]
        bell = Notification.objects.get(user=guide.user)
        assert (bell.title, bell.body) == ("New tour booked", "Sam Rivera, 2 people, Tue, Jan 8 at 4:00 PM.")

    def it_pushes_to_the_guide(guide: Member, hours: Any, django_capture_on_commit_callbacks: Any):
        signed_in(guide)
        with (
            patch("core.events.channels.PushAdapter.deliver", return_value=True) as push,
            django_capture_on_commit_callbacks(execute=True),
        ):
            book_tour(guide=guide, starts_at=FOUR, visitor=_visitor(1), now=NOW)
        assert push.call_count == 1

    def it_sends_the_part_5_seam_once_committed(guide: Member, hours: Any, django_capture_on_commit_callbacks: Any):
        heard: list[tuple[int, str]] = []

        def receiver(sender: object, booking: TourBooking, change: str, **extra: object) -> None:
            heard.append((booking.pk, change))

        signals.tour_booking_changed.connect(receiver)
        try:
            with django_capture_on_commit_callbacks(execute=False) as callbacks:
                booking = book_tour(guide=guide, starts_at=FOUR, visitor=_visitor(1), now=NOW)
            assert heard == []
            for callback in callbacks:
                callback()
        finally:
            signals.tour_booking_changed.disconnect(receiver)
        assert heard == [(booking.pk, signals.BOOKED)]


@pytest.fixture
def booked(guide: Member, hours: Any) -> TourBooking:
    return TourBookingFactory(guide=guide, starts_at=FOUR, party_size=2, visitor_email="sam@example.com")


def describe_reschedule_tour():
    def it_moves_the_booking_and_raises_its_sequence(guide: Member, booked: TourBooking):
        moved = reschedule_tour(booked, guide=guide, starts_at=FIVE, now=NOW)
        booked.refresh_from_db()
        assert (booked.starts_at, booked.ends_at, booked.sequence) == (FIVE, FIVE + timedelta(hours=1), 1)
        assert moved.pk == booked.pk
        four = open_tour_at(guide, FOUR, now=NOW)
        five = open_tour_at(guide, FIVE, now=NOW)
        assert four is not None and five is not None
        assert (four.seats_left, five.seats_left) == (4, 2)

    def it_moves_to_another_guides_tour(guide: Member, booked: TourBooking):
        other = make_guide(full_legal_name="Olive Other")
        TourHoursFactory(guide=other)
        reschedule_tour(booked, guide=other, starts_at=FOUR, now=NOW)
        booked.refresh_from_db()
        assert booked.guide == other

    def it_refuses_the_booking_own_time(guide: Member, booked: TourBooking):
        with pytest.raises(TourUnavailableError, match=SAME_TIME_MESSAGE):
            reschedule_tour(booked, guide=guide, starts_at=FOUR, now=NOW)

    def it_refuses_a_time_with_too_few_seats(guide: Member, booked: TourBooking):
        TourBookingFactory(guide=guide, starts_at=FIVE, party_size=3)
        with pytest.raises(TourFullError):
            reschedule_tour(booked, guide=guide, starts_at=FIVE, now=NOW)
        booked.refresh_from_db()
        assert (booked.starts_at, booked.sequence) == (FOUR, 0)

    def it_refuses_a_time_that_is_not_open(guide: Member, booked: TourBooking):
        with pytest.raises(TourUnavailableError, match=GONE_MESSAGE):
            reschedule_tour(booked, guide=guide, starts_at=local(TUESDAY, 19), now=NOW)

    def it_refuses_inside_the_cancel_cutoff(guide: Member, booked: TourBooking):
        with pytest.raises(TourLockedError) as refused:
            reschedule_tour(booked, guide=guide, starts_at=FIVE, now=FOUR - timedelta(hours=3))
        assert refused.value.reason == "too_close"

    def it_tells_the_visitor_and_the_guide(guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any):
        signed_in(guide)
        guide.refresh_from_db()
        with django_capture_on_commit_callbacks(execute=True):
            reschedule_tour(booked, guide=guide, starts_at=FIVE, now=NOW)
        visitor_mail = [message for message in mail.outbox if message.to == ["sam@example.com"]]
        assert [message.subject for message in visitor_mail] == ["Your Past Lives tour moved to Tue, Jan 8 at 5:00 PM"]
        invite = _ics(visitor_mail[0])
        assert f"UID:tour-{booked.pk}@pastlives" in invite
        assert "SEQUENCE:1" in invite
        assert "We moved it from Tuesday, January 8 at 4:00 PM." in visitor_mail[0].body
        guide_mail = [message for message in mail.outbox if message.to == [guide.user.email]]
        assert [message.subject for message in guide_mail] == [
            "Tour moved: Tue, Jan 8 at 4:00 PM to Tue, Jan 8 at 5:00 PM"
        ]
        assert Notification.objects.get(user=guide.user).title == "Tour moved"

    def it_tells_the_old_guide_it_left_and_the_new_guide_it_arrived(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        other = make_guide(full_legal_name="Olive Other")
        TourHoursFactory(guide=other)
        for member in (guide, other):
            signed_in(member)
            member.refresh_from_db()
        with django_capture_on_commit_callbacks(execute=True):
            reschedule_tour(booked, guide=other, starts_at=FOUR, now=NOW)
        old = [message for message in mail.outbox if message.to == [guide.user.email]]
        new = [message for message in mail.outbox if message.to == [other.user.email]]
        assert len(old) == 1 and "to another guide's time" in old[0].body
        assert [message.subject for message in new] == ["New tour booked: Tue, Jan 8 at 4:00 PM"]


def describe_cancel_tour_by_visitor():
    def it_cancels_and_frees_the_seats(guide: Member, booked: TourBooking):
        cancel_tour_by_visitor(booked, now=NOW)
        booked.refresh_from_db()
        assert (booked.status, booked.cancelled_at, booked.cancelled_by, booked.sequence) == (
            TourBooking.Status.CANCELLED,
            NOW,
            None,
            1,
        )
        tour = open_tour_at(guide, FOUR, now=NOW)
        assert tour is not None and tour.seats_left == 4

    def it_refuses_a_second_cancel(booked: TourBooking):
        cancel_tour_by_visitor(booked, now=NOW)
        with pytest.raises(TourLockedError) as refused:
            cancel_tour_by_visitor(booked, now=NOW)
        assert refused.value.reason == "cancelled"

    def it_tells_the_visitor_and_the_guide(guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any):
        signed_in(guide)
        guide.refresh_from_db()
        with django_capture_on_commit_callbacks(execute=True):
            cancel_tour_by_visitor(booked, now=NOW)
        visitor_mail = [message for message in mail.outbox if message.to == ["sam@example.com"]]
        assert [message.subject for message in visitor_mail] == [
            "Your Past Lives tour on Tue, Jan 8 at 4:00 PM is cancelled"
        ]
        invite = _ics(visitor_mail[0])
        assert "METHOD:CANCEL" in invite and "STATUS:CANCELLED" in invite and "SEQUENCE:1" in invite
        guide_mail = [message for message in mail.outbox if message.to == [guide.user.email]]
        assert [message.subject for message in guide_mail] == ["Tour cancelled: Tue, Jan 8 at 4:00 PM"]
        assert (
            Notification.objects.get(user=guide.user).body
            == f"{booked.visitor_name} cancelled their tour on Tue, Jan 8 at 4:00 PM."
        )


def describe_two_visitors_racing_for_the_last_seats():
    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_lets_one_book_and_tells_the_other_it_filled_up():
        guide = make_guide(full_legal_name="Gina Guide")
        TourHoursFactory(guide=guide, seats=2)
        # Made up front: a first load() inside each racing transaction would insert the row and
        # serialize the visitors on its primary key, hiding whether the hours lock works.
        FacilityTourSettings.load()
        real_open_tour_at = open_tour_at
        start = threading.Barrier(4)
        outcomes: list[str] = []

        def slow_open_tour_at(*args: Any, **kwargs: Any) -> Any:
            # Widen the gap between counting the seats and saving the row: without the row lock
            # every visitor would count 2 seats free here and all of them would book.
            tour = real_open_tour_at(*args, **kwargs)
            clock.sleep(0.3)
            return tour

        def visitor(number: int) -> None:
            try:
                start.wait()
                book_tour(guide=guide, starts_at=FOUR, visitor=_visitor(2, email=f"v{number}@example.com"), now=NOW)
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
        assert TourBooking.objects.seats_taken(guide, FOUR) == 2
