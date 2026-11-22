"""A guide's reschedule asks the visitor first (#1283): the proposal, the visitor's answer, who hears it."""

from __future__ import annotations

from datetime import UTC, time, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours import signals
from tours.booking import (
    ACCEPTED,
    KEPT,
    UNAVAILABLE,
    answer_tour_proposal,
    cancel_tour_by_visitor,
    propose_tour_time,
    reschedule_tour,
)
from tours.models import TourBooking

pytestmark = pytest.mark.django_db

HOST = "tours.pastlives.space"
DAY = timezone.localdate() + timedelta(days=3)
FOUR = local(DAY, 16)
FIVE = local(DAY, 17)
SIX = local(DAY, 18)
HTMX = {"HTTP_HX_REQUEST": "true"}


def _user(member: Member) -> User:
    signed_in(member)
    member.refresh_from_db()
    assert member.user is not None
    return member.user


def _to(address: str) -> list[Any]:
    return [message for message in mail.outbox if message.to == [address]]


def _visitor() -> Client:
    return Client(HTTP_HOST=HOST)


def _manage(booking: TourBooking) -> str:
    return reverse("tours_public:manage", args=[booking.self_serve_token])


@pytest.fixture
def booked(guide: Member) -> TourBooking:
    TourHoursFactory(guide=guide, weekday=DAY.weekday(), end_time=time(19, 0), seats=4)
    return TourBookingFactory(
        guide=guide, starts_at=FOUR, party_size=2, visitor_name="Sam Rivera", visitor_email="sam@example.com"
    )


@pytest.fixture
def other() -> Member:
    other = make_guide(full_legal_name="Olive Other")
    TourHoursFactory(guide=other, weekday=DAY.weekday(), seats=4)
    return other


def _propose(booking: TourBooking, guide: Member, actor: User, starts_at: Any = FIVE, **kwargs: Any) -> str:
    """Propose ``starts_at`` and return the proposal's key, with the email sent."""
    proposed = propose_tour_time(booking, guide=guide, starts_at=starts_at, actor=actor, **kwargs)
    return proposed.proposal_key


def describe_the_proposal_on_the_booking():
    def it_keys_the_proposal_by_when_it_was_made(booked: TourBooking):
        assert (booked.has_open_proposal, booked.proposal_key) == (False, "")
        booked.proposed_at = timezone.datetime(1970, 1, 1, 0, 0, 1, 5, tzinfo=UTC)
        assert booked.proposal_key == "1000005"

    def it_refuses_an_answer_that_no_longer_applies(guide: Member, booked: TourBooking):
        now = timezone.now()
        booked.propose_time(guide, FIVE, proposed_by=_user(guide), now=now)
        key = booked.proposal_key
        assert booked.proposal_refusal(key, now) == ""
        assert booked.proposal_refusal("", now) == "stale"
        assert booked.proposal_refusal("123", now) == "stale"
        assert booked.proposal_refusal(key, FIVE + timedelta(minutes=1)) == "already"
        booked.proposed_starts_at = now - timedelta(minutes=1)
        assert booked.proposal_refusal(key, now) == "passed"
        booked.status = TourBooking.Status.CANCELLED
        assert booked.proposal_refusal(key, now) == "already"


def describe_answering():
    def it_moves_on_accept_and_sends_the_moved_email_with_the_invite(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        guide_user = _user(guide)
        with django_capture_on_commit_callbacks(execute=True):
            key = _propose(booked, guide, guide_user)
        mail.outbox.clear()
        heard: list[dict[str, Any]] = []

        def receiver(sender: object, **kwargs: Any) -> None:
            heard.append(kwargs)

        signals.tour_booking_changed.connect(receiver)
        try:
            with django_capture_on_commit_callbacks(execute=True):
                assert answer_tour_proposal(booked, accept=True, proposal=key) == ACCEPTED
        finally:
            signals.tour_booking_changed.disconnect(receiver)
        booked.refresh_from_db()
        assert (booked.starts_at, booked.sequence, booked.has_open_proposal) == (FIVE, 1, False)
        (moved,) = _to("sam@example.com")
        assert moved.subject.startswith("Your Past Lives tour moved to")
        content = moved.attachments[0][1]
        assert "SEQUENCE:1" in (content.decode() if isinstance(content, bytes) else str(content))
        assert [change["change"] for change in heard] == [signals.MOVED]

    def it_tells_the_guide_who_proposed_once_by_email_and_bell(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        guide_user = _user(guide)
        with django_capture_on_commit_callbacks(execute=True):
            key = _propose(booked, guide, guide_user)
            answer_tour_proposal(booked, accept=True, proposal=key)
        (told,) = _to(guide_user.email)
        assert told.subject.startswith("New tour time accepted:")
        assert "Sam Rivera accepted the new time." in told.body
        (bell,) = Notification.objects.filter(user=guide_user)
        assert bell.title == "New tour time accepted"
        assert bell.url == f"/facility-tours/#tour-booking-{booked.pk}"

    def it_tells_the_admin_who_proposed_and_both_guides_on_a_reassignment(
        guide: Member, other: Member, admin: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        guide_user, other_user, admin_user = _user(guide), _user(other), _user(admin)
        with django_capture_on_commit_callbacks(execute=True):
            key = _propose(booked, other, admin_user, starts_at=FOUR)
            assert answer_tour_proposal(booked, accept=True, proposal=key) == ACCEPTED
        booked.refresh_from_db()
        assert booked.guide == other
        for user in (guide_user, other_user, admin_user):
            (told,) = _to(user.email)
            assert told.subject.startswith("New tour time accepted:")

    def it_tells_the_admin_once_on_a_tour_with_no_guide(
        admin: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        TourHoursFactory(guide=admin, weekday=DAY.weekday(), seats=4)
        TourBooking.objects.filter(pk=booked.pk).update(guide=None)
        booked.refresh_from_db()
        admin_user = _user(admin)
        with django_capture_on_commit_callbacks(execute=True):
            key = _propose(booked, admin, admin_user)
            assert answer_tour_proposal(booked, accept=True, proposal=key) == ACCEPTED
        assert len(_to(admin_user.email)) == 1

    def it_treats_a_proposal_whose_guide_is_gone_as_no_longer_open(
        guide: Member, other: Member, admin: Member, booked: TourBooking
    ):
        key = _propose(booked, other, _user(admin), starts_at=FOUR)
        TourBooking.objects.filter(pk=booked.pk).update(proposed_guide=None)
        assert answer_tour_proposal(booked, accept=True, proposal=key) == "stale"
        booked.refresh_from_db()
        assert booked.guide == guide

    def it_keeps_the_time_on_keep_and_tells_the_guide(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        guide_user = _user(guide)
        with django_capture_on_commit_callbacks(execute=True):
            key = _propose(booked, guide, guide_user)
        mail.outbox.clear()
        with django_capture_on_commit_callbacks(execute=True):
            assert answer_tour_proposal(booked, accept=False, proposal=key) == KEPT
        booked.refresh_from_db()
        assert (booked.starts_at, booked.sequence, booked.has_open_proposal) == (FOUR, 0, False)
        assert _to("sam@example.com") == []
        (told,) = _to(guide_user.email)
        assert "Sam Rivera kept their tour for 2 people on" in told.body
        assert Notification.objects.get(user=guide_user).title == "Tour time kept"

    def it_keeps_the_time_when_the_proposed_tour_filled_up(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        guide_user = _user(guide)
        key = _propose(booked, guide, guide_user)
        TourBookingFactory(guide=guide, starts_at=FIVE, party_size=3)
        with django_capture_on_commit_callbacks(execute=True):
            assert answer_tour_proposal(booked, accept=True, proposal=key) == UNAVAILABLE
        booked.refresh_from_db()
        assert (booked.starts_at, booked.has_open_proposal) == (FOUR, False)
        assert Notification.objects.get(user=guide_user).title == "New tour time no longer open"

    def it_refuses_a_replaced_proposal(guide: Member, booked: TourBooking):
        actor = _user(guide)
        first = _propose(booked, guide, actor, now=timezone.now() - timedelta(minutes=5))
        second = _propose(booked, guide, actor, starts_at=SIX)
        assert answer_tour_proposal(booked, accept=True, proposal=first) == "stale"
        booked.refresh_from_db()
        assert (booked.starts_at, booked.proposal_key) == (FOUR, second)

    def it_refuses_a_second_answer_and_a_cancelled_tour(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        assert answer_tour_proposal(booked, accept=False, proposal=key) == KEPT
        assert answer_tour_proposal(booked, accept=True, proposal=key) == "stale"
        key = _propose(booked, guide, booked.guide.user)
        cancel_tour_by_visitor(booked)
        assert answer_tour_proposal(booked, accept=True, proposal=key) == "already"

    def it_drops_the_proposal_when_the_visitor_moves_themselves(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        reschedule_tour(booked, guide=guide, starts_at=SIX)
        booked.refresh_from_db()
        assert (booked.starts_at, booked.has_open_proposal) == (SIX, False)
        assert answer_tour_proposal(booked, accept=True, proposal=key) == "stale"


def describe_the_visitor_pages():
    def it_opens_the_emailed_link_with_both_answers_and_changes_nothing(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        content = _visitor().get(_manage(booked), {"proposal": key, "answer": "accept"}).content.decode()
        assert "Your Guide Asked to Move Your Tour" in content
        assert 'data-tour-answer="accept"' in content and 'data-tour-answer="keep"' in content
        assert f'name="proposal" value="{key}"' in content
        assert "pl-tourpub__answers--keep" not in content and "data-tour-proposal-closed" not in content
        booked.refresh_from_db()
        assert (booked.starts_at, booked.proposal_key) == (FOUR, key)

    def it_puts_keep_first_when_keep_was_tapped(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        content = _visitor().get(_manage(booked), {"proposal": key, "answer": "keep"}).content.decode()
        assert "pl-tourpub__answers--keep" in content

    def it_accepts_and_lands_on_the_moved_tour(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        response = _visitor().post(_manage(booked), {"proposal": key, "answer": "accept"})
        assert (response.status_code, response["Location"]) == (302, f"{_manage(booked)}?moved=1")
        booked.refresh_from_db()
        assert booked.starts_at == FIVE

    def it_keeps_and_lands_on_the_kept_tour(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        response = _visitor().post(_manage(booked), {"proposal": key, "answer": "keep"}, follow=True)
        content = response.content.decode()
        assert "You Kept Your Time" in content and "data-tour-kept" in content
        assert "data-tour-proposal" not in content

    def it_says_so_when_the_new_time_filled_up(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        TourBookingFactory(guide=guide, starts_at=FIVE, party_size=3)
        response = _visitor().post(_manage(booked), {"proposal": key, "answer": "accept"}, follow=True)
        content = response.content.decode()
        assert "data-tour-unavailable" in content and "you keep your current time" in content
        booked.refresh_from_db()
        assert booked.starts_at == FOUR

    def it_says_a_replaced_proposal_is_no_longer_open(guide: Member, booked: TourBooking):
        actor = _user(guide)
        first = _propose(booked, guide, actor, now=timezone.now() - timedelta(minutes=5))
        second = _propose(booked, guide, actor, starts_at=SIX)
        for response in (
            _visitor().get(_manage(booked), {"proposal": first, "answer": "accept"}),
            _visitor().post(_manage(booked), {"proposal": first, "answer": "accept"}),
        ):
            content = response.content.decode()
            assert response.status_code == 200
            assert 'data-tour-proposal-closed="stale"' in content
            assert "it was already answered or replaced" in content
            # The newer proposal is the one the page offers.
            assert f'name="proposal" value="{second}"' in content
        booked.refresh_from_db()
        assert booked.starts_at == FOUR

    def it_says_an_answered_proposal_is_no_longer_open_and_offers_nothing(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        answer_tour_proposal(booked, accept=False, proposal=key)
        content = _visitor().get(_manage(booked), {"proposal": key, "answer": "accept"}).content.decode()
        assert 'data-tour-proposal-closed="stale"' in content and "data-tour-answer" not in content

    def it_says_when_the_tour_was_cancelled_since(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        cancel_tour_by_visitor(booked)
        content = _visitor().get(_manage(booked), {"proposal": key}).content.decode()
        assert 'data-tour-proposal-closed="already"' in content

    def it_ignores_a_post_with_no_answer(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        content = _visitor().post(_manage(booked), {"proposal": key, "answer": "maybe"}).content.decode()
        assert "Your Guide Asked to Move Your Tour" in content
        booked.refresh_from_db()
        assert (booked.starts_at, booked.proposal_key) == (FOUR, key)

    def it_reports_a_proposal_answered_while_the_page_was_open(guide: Member, booked: TourBooking):
        key = _propose(booked, guide, _user(guide))
        with patch("tours.public_views.answer_tour_proposal", return_value="stale"):
            content = _visitor().post(_manage(booked), {"proposal": key, "answer": "accept"}).content.decode()
        assert 'data-tour-proposal-closed="stale"' in content

    def it_shows_the_proposal_with_both_answers_without_a_link(
        guide: Member, other: Member, admin: Member, booked: TourBooking
    ):
        assert "data-tour-proposal" not in _visitor().get(_manage(booked)).content.decode()
        key = _propose(booked, other, _user(admin), starts_at=FIVE)
        content = _visitor().get(_manage(booked)).content.decode()
        assert "Your Guide Asked to Move Your Tour" in content
        assert f'name="proposal" value="{key}"' in content
        assert "With Olive as your guide" in content


def describe_the_guides_page():
    def it_shows_the_pending_proposal_on_the_row_and_in_the_modal(guide: Member, booked: TourBooking):
        client = signed_in(guide)
        assert "data-tour-proposal" not in client.get(reverse("tours:upcoming")).content.decode()
        _propose(booked, guide, _user(guide))
        row = client.get(reverse("tours:upcoming")).content.decode()
        assert f'data-tour-proposal="{booked.pk}"' in row and "Waiting for the Visitor" in row
        assert f"Asked to move to {timezone.localtime(FIVE):%a, %b} {FIVE.day} · 5:00 PM" in row
        modal = client.get(reverse("tours:booking_reschedule", args=[booked.pk]), **HTMX).content.decode()
        assert "data-tour-move-pending" in modal and "Asking for another time replaces that one." in modal

    def it_names_the_other_guide_on_a_proposed_reassignment(admin: Member, other: Member, booked: TourBooking):
        _propose(booked, other, _user(admin), starts_at=FOUR)
        row = signed_in(admin).get(reverse("tours:upcoming")).content.decode()
        assert "with Olive Other" in row

    def it_says_the_modal_asks_the_visitor(guide: Member, booked: TourBooking):
        key = f"{guide.pk}-{FIVE.astimezone(UTC):%Y%m%dT%H%MZ}"
        content = (
            signed_in(guide)
            .get(reverse("tours:booking_reschedule", args=[booked.pk]), {"tour": key}, **HTMX)
            .content.decode()
        )
        assert "Ask the Visitor" in content and "data-tour-move-ask" in content
        assert "Move Tour" not in content
