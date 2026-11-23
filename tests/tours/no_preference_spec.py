"""A No preference request shared between guides (#1255): the first to confirm gets it.

Visitors can no longer send one (#1312 removed the pool: hours may overlap and the first booking
claims the time), but a request sent before stays answerable until migration 0012's task books it,
so the answering machinery stays and these specs build the shared rows directly.
"""

from __future__ import annotations

import threading
import time as clock
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours import booking as booking_service
from tours.availability import open_tour_at
from tours.booking import (
    TourLockedError,
    cancel_tour_by_visitor,
    confirm_tour_request,
    decline_tour_request,
    lapse_request,
)
from tours.models import TourBooking, TourGuideSettings
from tours.notify import shared_with_label

pytestmark = pytest.mark.django_db

# 2030-01-07 is a Monday; both guides give tours Tuesday the 8th, 4:00 and 5:00 PM.
NOW = local(date(2030, 1, 7), 9)
TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)
HOST = "tours.pastlives.space"


def _guide(name: str) -> Member:
    """A Confirm First Tour Guide with an account and Tuesday hours."""
    member = make_guide(full_legal_name=name)
    TourHoursFactory(guide=member, seats=4)
    TourGuideSettings.objects.create(guide=member, confirm_mode=TourGuideSettings.ConfirmMode.FIRST)
    signed_in(member)
    member.refresh_from_db()
    return member


def _shared_request(guides: tuple[Member, ...], starts_at: Any) -> list[TourBooking]:
    """One pending row per guide sharing a ``request_group``, as the pool made them before #1312."""
    group = uuid4()
    return [
        TourBookingFactory(
            guide=member,
            starts_at=starts_at,
            party_size=2,
            visitor_name="Sam Rivera",
            visitor_email="sam@example.com",
            status=TourBooking.Status.PENDING,
            request_group=group,
        )
        for member in guides
    ]


def _to(address: str) -> list[Any]:
    return [message for message in mail.outbox if message.to == [address]]


@pytest.fixture
def alex() -> Member:
    return _guide("Alex Able")


@pytest.fixture
def gina() -> Member:
    return _guide("Gina Guide")


@pytest.fixture
def shared(alex: Member, gina: Member) -> list[TourBooking]:
    """A No preference request sent to Alex and Gina, both Confirm First; the outbox starts empty."""
    rows = _shared_request((alex, gina), FOUR)
    mail.outbox.clear()
    return rows


def describe_the_first_guide_to_confirm():
    def it_gets_the_tour_and_the_other_guide_is_told(
        shared: list[TourBooking], alex: Member, gina: Member, django_capture_on_commit_callbacks: Any
    ):
        holder, other = shared
        token = holder.self_serve_token
        with django_capture_on_commit_callbacks(execute=True):
            won = confirm_tour_request(other, actor=gina.user, now=NOW)
        holder.refresh_from_db()
        assert (won.status, won.request_group, won.self_serve_token) == (TourBooking.Status.BOOKED, None, token)
        assert holder.status == TourBooking.Status.CANCELLED and holder.cancelled_by == gina.user
        assert holder.self_serve_token != token
        assert TourBooking.objects.seats_taken(alex, FOUR) == 0
        (visitor,) = _to("sam@example.com")
        assert "with Gina as your guide" in visitor.body and token in visitor.body
        (to_alex,) = _to(alex.user.email)
        assert to_alex.subject.startswith("Tour request taken:")
        assert "Gina confirmed Sam Rivera's request" in to_alex.body
        assert _to(gina.user.email) == []

    def it_keeps_the_visitors_link_when_the_first_row_wins(shared: list[TourBooking], alex: Member):
        holder, other = shared
        token = holder.self_serve_token
        won = confirm_tour_request(holder, actor=alex.user, now=NOW)
        other.refresh_from_db()
        assert won.self_serve_token == token and other.status == TourBooking.Status.CANCELLED

    def it_refuses_a_late_answer_from_the_other_guide(shared: list[TourBooking], alex: Member, gina: Member):
        holder, other = shared
        confirm_tour_request(holder, actor=alex.user, now=NOW)
        for answer in (confirm_tour_request, decline_tour_request):
            with pytest.raises(TourLockedError) as refused:
                answer(other, actor=gina.user, now=NOW)
            assert refused.value.reason == "taken"


def describe_declining_a_shared_request():
    def it_stays_with_the_other_guide_until_the_last_declines(
        shared: list[TourBooking], alex: Member, gina: Member, django_capture_on_commit_callbacks: Any
    ):
        holder, other = shared
        token = holder.self_serve_token
        with django_capture_on_commit_callbacks(execute=True):
            decline_tour_request(holder, actor=alex.user, message="Away", now=NOW)
        other.refresh_from_db()
        assert other.is_request and other.self_serve_token == token
        assert mail.outbox == []
        with django_capture_on_commit_callbacks(execute=True):
            decline_tour_request(other, actor=gina.user, message="Sorry", now=NOW)
        (visitor,) = _to("sam@example.com")
        assert "Sorry" in visitor.body and "as your guide" not in visitor.body


def describe_a_shared_request_lapsing():
    def it_lapses_for_every_guide_and_tells_the_visitor_once(
        shared: list[TourBooking], alex: Member, gina: Member, django_capture_on_commit_callbacks: Any
    ):
        late = shared[1].confirm_by
        with django_capture_on_commit_callbacks(execute=True):
            lapsed = lapse_request(shared[1].pk, now=late)
            assert lapse_request(shared[0].pk, now=late) is None
        assert lapsed == shared[0]
        assert set(TourBooking.objects.values_list("status", flat=True)) == {TourBooking.Status.CANCELLED}
        assert len(_to("sam@example.com")) == 1
        assert [len(_to(member.user.email)) for member in (alex, gina)] == [1, 1]


def describe_the_visitor_cancelling():
    def it_withdraws_every_guides_request(
        shared: list[TourBooking], alex: Member, gina: Member, django_capture_on_commit_callbacks: Any
    ):
        with django_capture_on_commit_callbacks(execute=True):
            cancel_tour_by_visitor(shared[0], now=NOW)
        assert set(TourBooking.objects.values_list("status", flat=True)) == {TourBooking.Status.CANCELLED}
        assert len(_to("sam@example.com")) == 1
        for member in (alex, gina):
            (told,) = _to(member.user.email)
            assert told.subject.startswith("Tour cancelled:")


def describe_the_visitor_cancelling_after_the_link_moved():
    def it_cancels_the_row_that_holds_the_link_now(shared: list[TourBooking], alex: Member):
        stale = TourBooking.objects.get(pk=shared[0].pk)
        decline_tour_request(shared[0], actor=alex.user, now=NOW)
        cancelled = cancel_tour_by_visitor(stale, now=NOW)
        assert cancelled.pk == shared[1].pk
        assert set(TourBooking.objects.values_list("status", flat=True)) == {TourBooking.Status.CANCELLED}


def describe_the_visitor_cancelling_after_a_guide_confirmed():
    def it_cancels_the_booked_tour_their_link_now_names(shared: list[TourBooking], gina: Member):
        stale = TourBooking.objects.get(pk=shared[0].pk)
        confirm_tour_request(shared[1], actor=gina.user, now=NOW)
        cancelled = cancel_tour_by_visitor(stale, now=NOW)
        assert (cancelled.pk, cancelled.status) == (shared[1].pk, TourBooking.Status.CANCELLED)
        assert open_tour_at(gina, FOUR, now=NOW, staff=True).seats_left == 4


def describe_the_pages():
    @pytest.fixture
    def soon(alex: Member, gina: Member) -> list[TourBooking]:
        """A shared request four days out, so the live pages' clock admits it."""
        return _shared_request((alex, gina), local(timezone.localdate() + timedelta(days=4), 16))

    def it_names_no_guide_to_the_visitor_until_one_confirms(soon: list[TourBooking], gina: Member):
        url = reverse("tours_public:manage", args=[soon[0].self_serve_token])
        page = Client(HTTP_HOST=HOST).get(url).content.decode()
        assert "We sent your request to every guide" in page and "with Alex" not in page
        confirm_tour_request(soon[1], actor=gina.user)
        assert "with Gina" in Client(HTTP_HOST=HOST).get(url).content.decode()

    def it_tells_each_guide_on_upcoming_who_else_has_it(soon: list[TourBooking], alex: Member):
        page = signed_in(alex).get(reverse("tours:upcoming")).content.decode()
        assert "No preference: also sent to Gina. Whoever confirms first gets the tour." in page
        assert "It stays with the other guides" in page

    def it_says_another_guide_took_it(soon: list[TourBooking], alex: Member, gina: Member):
        confirm_tour_request(soon[0], actor=alex.user)
        response = signed_in(gina).post(reverse("tours:booking_confirm", args=[soon[1].pk]))
        assert [str(m) for m in get_messages(response.wsgi_request)] == ["Another guide already took this request."]


def describe_naming_the_other_guides():
    def it_joins_one_two_or_more_names():
        assert [shared_with_label(names) for names in [(), ("Alex",), ("Alex", "Sam"), ("Alex", "Sam", "Jo")]] == [
            "",
            "Alex",
            "Alex and Sam",
            "Alex, Sam and Jo",
        ]

    def it_fills_only_the_shared_rows_of_a_list(shared: list[TourBooking], alex: Member):
        plain = TourBookingFactory(guide=alex, starts_at=FOUR + timedelta(hours=1))
        listed = [*shared, plain]
        TourBooking.attach_shared_with(listed)
        assert [row.shared_with for row in listed] == [("Gina",), ("Alex",), ()]
        TourBooking.attach_shared_with([plain])
        assert plain.shared_with == ()


def describe_two_guides_confirming_at_once():
    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_books_exactly_one():
        alex = _guide("Alex Able")
        gina = _guide("Gina Guide")
        rows = _shared_request((alex, gina), FOUR)
        real_lock = booking_service._lock_group
        start = threading.Barrier(2)
        outcomes: list[str] = []

        def slow_lock(booking: TourBooking) -> list[TourBooking]:
            group = real_lock(booking)
            clock.sleep(0.3)
            return group

        def answer(row: TourBooking, member: Member) -> None:
            try:
                start.wait()
                confirm_tour_request(row, actor=member.user, now=NOW)
                outcomes.append("booked")
            except TourLockedError as exc:
                outcomes.append(exc.reason)
            finally:
                connection.close()

        with patch.object(booking_service, "_lock_group", slow_lock):
            threads = [threading.Thread(target=answer, args=pair) for pair in zip(rows, (alex, gina), strict=True)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert sorted(outcomes) == ["booked", "taken"]
        assert TourBooking.objects.filter(status=TourBooking.Status.BOOKED).count() == 1
