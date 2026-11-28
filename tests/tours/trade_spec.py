"""Guides trade tours with each other (#1308): Trade this tour, Take this tour, and who hears what."""

from __future__ import annotations

from datetime import time, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone

from core.events.registry import Channel, ChannelDefault, get_event
from core.models import Notification
from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourCalendarConnectionFactory, TourHoursFactory, local
from tours import signals
from tours.booking import (
    SAME_GUIDE_MESSAGE,
    TRADE_OPEN_MESSAGE,
    NotYourTradeError,
    TradeRefusedError,
    answer_trade,
    cancel_tour_by_staff,
    offer_take,
    offer_trade,
    propose_tour_time,
    answer_tour_proposal,
    withdraw_trade,
)
from tours.forms import MAX_TRADE_NOTE
from tours.models import TourBooking, TourCalendarSync
from tours.notify import short_when

pytestmark = pytest.mark.django_db

UPCOMING = reverse("tours:upcoming")
HTMX = {"HTTP_HX_REQUEST": "true"}
DAY = timezone.localdate() + timedelta(days=3)
FOUR = local(DAY, 16)
FIVE = local(DAY, 17)


def _user(member: Member) -> User:
    signed_in(member)
    member.refresh_from_db()
    assert member.user is not None
    return member.user


def _to(member_or_address: Member | str) -> list[Any]:
    address = member_or_address if isinstance(member_or_address, str) else _user(member_or_address).email
    return [message for message in mail.outbox if message.to == [address]]


def _bells(member: Member) -> list[str]:
    return list(Notification.objects.filter(user=_user(member)).order_by("pk").values_list("title", flat=True))


def _flashes(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _url(name: str, booking: TourBooking) -> str:
    return reverse(f"tours:{name}", args=[booking.pk])


@pytest.fixture
def dayzie() -> Member:
    member = make_guide(full_legal_name="Dayzie Dove")
    _user(member)
    return member


@pytest.fixture
def third() -> Member:
    member = make_guide(full_legal_name="Theo Third")
    _user(member)
    return member


@pytest.fixture
def booked(guide: Member) -> TourBooking:
    _user(guide)  # every guide signs in, so their bells and emails have an account to reach
    TourHoursFactory(guide=guide, weekday=DAY.weekday(), end_time=time(19, 0), seats=4)
    return TourBookingFactory(
        guide=guide, starts_at=FOUR, party_size=2, visitor_name="Sam Rivera", visitor_email="sam@example.com"
    )


@pytest.fixture
def committed(django_capture_on_commit_callbacks: Any) -> Any:
    """Run every after commit callback (the notices and the seam) as the transaction commits."""
    return lambda: django_capture_on_commit_callbacks(execute=True)


def _offer(booking: TourBooking, to: Member, by: Member, committed: Any, note: str = "") -> str:
    with committed():
        return offer_trade(booking, to=to, actor=_user(by), note=note).trade_key


def describe_the_event():
    def it_is_a_tour_guide_event_with_bell_email_and_push_on():
        event = get_event("tour.trade")
        assert event.category == "Tours"
        for channel in (Channel.IN_APP, Channel.EMAIL, Channel.PUSH):
            spec = event.channel(channel)
            assert spec is not None and spec.default is ChannelDefault.ON


def describe_offer_trade():
    def it_records_the_offer_without_moving_the_tour(guide: Member, dayzie: Member, booked: TourBooking, committed):
        key = _offer(booked, dayzie, guide, committed, note="  Class that day  ")
        booked.refresh_from_db()
        assert (booked.guide, booked.starts_at, booked.sequence) == (guide, FOUR, 0)
        assert (booked.trade_to, booked.trade_kind, booked.trade_note, booked.trade_key) == (
            dayzie,
            TourBooking.TradeKind.TRADE,
            "Class that day",
            key,
        )
        assert booked.trade_answerer_id == dayzie.pk and booked.trade_asker_id == guide.pk

    def it_tells_the_other_guide_by_bell_and_email_naming_the_sender_time_and_note(
        guide: Member, dayzie: Member, booked: TourBooking, committed
    ):
        _offer(booked, dayzie, guide, committed, note="Class that day")
        (bell,) = Notification.objects.filter(user=_user(dayzie))
        assert bell.title == "Tour trade offered"
        assert bell.body == f'Gina wants to trade you Sam Rivera\'s tour on {short_when(FOUR)}. "Class that day"'
        assert bell.url == f"{UPCOMING}#tour-booking-{booked.pk}"
        (email,) = _to(dayzie)
        assert email.subject == f"Tour trade offered: {short_when(FOUR)}"
        assert "Class that day" in email.body and f"#tour-booking-{booked.pk}" in email.body
        # The email only opens the row: no link in it acts on the trade (mail scanners open links).
        assert "trade/answer" not in email.body and "sam@example.com" not in email.body
        assert _to("sam@example.com") == []

    def it_refuses_the_tours_own_guide_a_cancelled_and_a_started_tour(
        guide: Member, dayzie: Member, booked: TourBooking
    ):
        with pytest.raises(TradeRefusedError, match=SAME_GUIDE_MESSAGE):
            offer_trade(booked, to=guide, actor=_user(guide))
        with pytest.raises(TradeRefusedError, match="already started"):
            offer_trade(booked, to=dayzie, actor=_user(guide), now=FOUR)
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.CANCELLED)
        with pytest.raises(TradeRefusedError, match="was cancelled"):
            offer_trade(booked, to=dayzie, actor=_user(guide))

    def it_replaces_an_open_offer_and_tells_the_replaced_guide(
        guide: Member, dayzie: Member, third: Member, booked: TourBooking, committed
    ):
        first = _offer(booked, dayzie, guide, committed)
        with patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(seconds=1)):
            _offer(booked, third, guide, committed)
        booked.refresh_from_db()
        assert booked.trade_to == third
        assert _bells(dayzie) == ["Tour trade offered", "Tour trade withdrawn"]
        with pytest.raises(TradeRefusedError, match="This trade was withdrawn."):
            answer_trade(booked, accept=True, key=first, answerer=dayzie)
        booked.refresh_from_db()
        assert booked.guide == guide


def describe_accept():
    def it_moves_the_tour_bells_the_original_guide_and_tells_the_guest_once(
        guide: Member, dayzie: Member, booked: TourBooking, committed
    ):
        key = _offer(booked, dayzie, guide, committed)
        sent: list[dict[str, Any]] = []
        signals.tour_booking_changed.connect(lambda **kwargs: sent.append(kwargs), weak=False, dispatch_uid="t1308")
        try:
            with committed():
                answer_trade(booked, accept=True, key=key, answerer=dayzie)
        finally:
            signals.tour_booking_changed.disconnect(dispatch_uid="t1308")
        booked.refresh_from_db()
        assert (booked.guide, booked.starts_at, booked.sequence, booked.has_open_trade) == (dayzie, FOUR, 0, False)
        assert [(kw["change"], kw["previous_guide"], kw["previous_starts_at"]) for kw in sent] == [
            (signals.MOVED, guide, FOUR)
        ]
        assert _bells(guide) == ["Tour trade accepted"]
        assert _to(guide) == []  # a bell only
        (guest,) = _to("sam@example.com")
        assert guest.subject == "Your Past Lives tour guide is now Dayzie"
        assert "Your guide is now Dayzie." in guest.body and "Dove" not in guest.body
        assert "/reschedule/" in guest.body and "/cancel/" in guest.body
        assert not guest.attachments  # no new invite: it names no guide

    def it_moves_the_google_event_to_the_new_guides_calendar(
        guide: Member, dayzie: Member, booked: TourBooking, committed
    ):
        old = TourCalendarConnectionFactory(guide=guide)
        TourCalendarConnectionFactory(guide=dayzie, google_email="dayzie@example.com")
        TourCalendarSync.objects.create(booking=booked, connection=old, event_id="ev1", due=False)
        key = _offer(booked, dayzie, guide, committed)
        assert not TourCalendarSync.objects.get(booking=booked).due  # an offer moves nothing
        with committed():
            answer_trade(booked, accept=True, key=key, answerer=dayzie)
        assert TourCalendarSync.objects.get(booking=booked).due

    def it_refuses_anyone_but_the_guide_it_was_offered_to(
        guide: Member, dayzie: Member, admin: Member, booked: TourBooking, committed
    ):
        key = _offer(booked, dayzie, guide, committed)
        for member in (guide, admin):
            with pytest.raises(NotYourTradeError):
                answer_trade(booked, accept=True, key=key, answerer=member)
        booked.refresh_from_db()
        assert (booked.guide, booked.trade_to) == (guide, dayzie)

    def it_moves_the_tour_once_when_two_accepts_race(guide: Member, dayzie: Member, booked: TourBooking, committed):
        key = _offer(booked, dayzie, guide, committed)
        with committed():
            answer_trade(booked, accept=True, key=key, answerer=dayzie)
        with pytest.raises(TradeRefusedError, match="This trade was withdrawn."):
            answer_trade(booked, accept=True, key=key, answerer=dayzie)
        assert len(_to("sam@example.com")) == 1

    def it_lapses_when_the_tour_starts_is_cancelled_or_moves_to_another_time(
        guide: Member, dayzie: Member, booked: TourBooking, committed
    ):
        key = _offer(booked, dayzie, guide, committed)
        with pytest.raises(TradeRefusedError, match="already started"):
            answer_trade(booked, accept=True, key=key, answerer=dayzie, now=FOUR)
        with committed():
            proposed = propose_tour_time(booked, guide=guide, starts_at=FIVE, actor=_user(guide))
            answer_tour_proposal(booked, accept=True, proposal=proposed.proposal_key)
        booked.refresh_from_db()
        assert (booked.starts_at, booked.has_open_trade) == (FIVE, False)
        with pytest.raises(TradeRefusedError, match="This trade was withdrawn."):
            answer_trade(booked, accept=True, key=key, answerer=dayzie)
        key = _offer(booked, dayzie, guide, committed)
        cancel_tour_by_staff(booked, actor=_user(guide))
        with pytest.raises(TradeRefusedError, match="was cancelled"):
            answer_trade(booked, accept=True, key=key, answerer=dayzie)
        booked.refresh_from_db()
        assert booked.guide == guide
        assert not [message for message in _to("sam@example.com") if "guide is now" in message.subject]


def describe_review_fixes():
    def it_drops_the_old_guides_open_new_time_on_accept(guide: Member, dayzie: Member, booked: TourBooking, committed):
        """A new time asked of the visitor was on the old guide's tours; accepting it later must not hand the tour back."""
        with committed():
            proposal = propose_tour_time(booked, guide=guide, starts_at=FIVE, actor=_user(guide)).proposal_key
        key = _offer(booked, dayzie, guide, committed)
        with committed():
            answer_trade(booked, accept=True, key=key, answerer=dayzie)
        booked.refresh_from_db()
        assert (booked.guide, booked.has_open_proposal) == (dayzie, False)
        assert answer_tour_proposal(booked, accept=True, proposal=proposal) == "stale"
        booked.refresh_from_db()
        assert (booked.guide, booked.starts_at) == (dayzie, FOUR)

    def it_tells_the_taker_when_a_new_offer_replaces_their_take(
        guide: Member, dayzie: Member, third: Member, booked: TourBooking, committed
    ):
        with committed():
            offer_take(booked, taker=dayzie, actor=_user(dayzie))
        with patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(seconds=1)):
            _offer(booked, third, guide, committed)
        assert _bells(dayzie) == ["Tour trade withdrawn"]
        assert _bells(guide) == ["Tour take asked"]


def describe_decline_and_withdraw():
    def it_declines_leaving_the_tour_and_telling_the_original_guide_by_bell_and_email(
        guide: Member, dayzie: Member, booked: TourBooking, committed
    ):
        key = _offer(booked, dayzie, guide, committed)
        with committed():
            answer_trade(booked, accept=False, key=key, answerer=dayzie)
        booked.refresh_from_db()
        assert (booked.guide, booked.has_open_trade) == (guide, False)
        assert _bells(guide) == ["Tour trade declined"]
        (email,) = _to(guide)
        assert email.subject == f"Tour trade declined: {short_when(FOUR)}" and "Nothing moved." in email.body
        assert _to("sam@example.com") == []

    def it_withdraws_for_the_original_guide_only_and_bells_the_recipient(
        guide: Member, dayzie: Member, admin: Member, booked: TourBooking, committed
    ):
        key = _offer(booked, dayzie, guide, committed)
        for member in (dayzie, admin):
            with pytest.raises(NotYourTradeError):
                withdraw_trade(booked, key=key, asker=member)
        with committed():
            withdraw_trade(booked, key=key, asker=guide)
        booked.refresh_from_db()
        assert (booked.guide, booked.has_open_trade) == (guide, False)
        assert _bells(dayzie) == ["Tour trade offered", "Tour trade withdrawn"]
        assert _to("sam@example.com") == []
        with pytest.raises(TradeRefusedError, match="This trade was withdrawn."):
            answer_trade(booked, accept=True, key=key, answerer=dayzie)


def describe_trade_back_and_on():
    def it_trades_back_to_the_first_guide_and_on_to_a_third(
        guide: Member, dayzie: Member, third: Member, booked: TourBooking, committed
    ):
        key = _offer(booked, dayzie, guide, committed)
        with committed():
            answer_trade(booked, accept=True, key=key, answerer=dayzie)
        with patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(seconds=1)):
            key = _offer(booked, guide, dayzie, committed)
            with committed():
                answer_trade(booked, accept=True, key=key, answerer=guide)
        booked.refresh_from_db()
        assert booked.guide == guide
        with patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(seconds=2)):
            key = _offer(booked, third, guide, committed)
            with committed():
                answer_trade(booked, accept=True, key=key, answerer=third)
        booked.refresh_from_db()
        assert booked.guide == third
        subjects = [message.subject for message in _to("sam@example.com")]
        assert subjects == [
            "Your Past Lives tour guide is now Dayzie",
            "Your Past Lives tour guide is now Gina",
            "Your Past Lives tour guide is now Theo",
        ]


def describe_take():
    def it_asks_the_tours_guide_who_approves_and_the_tour_moves(
        guide: Member, dayzie: Member, booked: TourBooking, committed
    ):
        with committed():
            key = offer_take(booked, taker=dayzie, actor=_user(dayzie), note="I'm in that day").trade_key
        booked.refresh_from_db()
        assert (booked.guide, booked.trade_to, booked.is_take) == (guide, dayzie, True)
        assert booked.trade_answerer_id == guide.pk and booked.trade_asker_id == dayzie.pk
        assert _bells(guide) == ["Tour take asked"]
        (ask,) = _to(guide)
        assert "I'm in that day" in ask.body and "Approve and Decline" in ask.body
        with pytest.raises(NotYourTradeError):
            answer_trade(booked, accept=True, key=key, answerer=dayzie)
        with committed():
            answer_trade(booked, accept=True, key=key, answerer=guide)
        booked.refresh_from_db()
        assert booked.guide == dayzie
        assert _bells(dayzie) == ["Tour is yours"]
        assert [message.subject for message in _to("sam@example.com")] == ["Your Past Lives tour guide is now Dayzie"]

    def it_declines_a_take_telling_the_taker(guide: Member, dayzie: Member, booked: TourBooking, committed):
        with committed():
            key = offer_take(booked, taker=dayzie, actor=_user(dayzie)).trade_key
            answer_trade(booked, accept=False, key=key, answerer=guide)
        booked.refresh_from_db()
        assert (booked.guide, booked.has_open_trade) == (guide, False)
        assert _bells(dayzie) == ["Tour take declined"]
        assert len(_to(dayzie)) == 1 and _to("sam@example.com") == []

    def it_lets_the_taker_withdraw(guide: Member, dayzie: Member, booked: TourBooking, committed):
        with committed():
            key = offer_take(booked, taker=dayzie, actor=_user(dayzie)).trade_key
        with pytest.raises(NotYourTradeError):
            withdraw_trade(booked, key=key, asker=guide)
        with committed():
            withdraw_trade(booked, key=key, asker=dayzie)
        assert _bells(guide) == ["Tour take asked", "Tour trade withdrawn"]

    def it_refuses_its_own_guide_a_tour_with_no_guide_and_one_with_a_trade_open(
        guide: Member, dayzie: Member, third: Member, booked: TourBooking
    ):
        with pytest.raises(TradeRefusedError, match=SAME_GUIDE_MESSAGE):
            offer_take(booked, taker=guide, actor=_user(guide))
        offer_trade(booked, to=third, actor=_user(guide))
        with pytest.raises(TradeRefusedError, match=TRADE_OPEN_MESSAGE):
            offer_take(booked, taker=dayzie, actor=_user(dayzie))
        TourBooking.objects.filter(pk=booked.pk).update(guide=None)
        with pytest.raises(TradeRefusedError, match=SAME_GUIDE_MESSAGE):
            offer_take(booked, taker=dayzie, actor=_user(dayzie))


def describe_the_upcoming_tab():
    def it_offers_trade_to_whoever_may_act_and_take_to_another_guide(
        guide: Member, dayzie: Member, admin: Member, booked: TourBooking
    ):
        own = signed_in(guide).get(UPCOMING).content.decode()
        assert f'data-tour-trade-open="{booked.pk}" data-guidance-target="tours.trade">Trade this tour<' in own
        assert f'data-tour-take="{booked.pk}"' not in own
        theirs = signed_in(dayzie).get(UPCOMING).content.decode()
        assert (
            f'data-tour-take="{booked.pk}">Take this tour<' in theirs and f'id="tour-take-{booked.pk}-note"' in theirs
        )
        assert f'data-tour-trade-open="{booked.pk}"' not in theirs
        assert f'data-tour-trade-open="{booked.pk}"' in signed_in(admin).get(UPCOMING).content.decode()

    def it_shows_both_guides_the_waiting_trade_with_accept_for_one_and_withdraw_for_the_other(
        guide: Member, dayzie: Member, booked: TourBooking
    ):
        offer_trade(booked, to=dayzie, actor=_user(guide), note="Class that day")
        own = signed_in(guide).get(UPCOMING).content.decode()
        assert "Trade offered to Dayzie, waiting" in own
        assert f'data-tour-trade-withdraw="{booked.pk}"' in own and "data-tour-trade-accept" not in own
        theirs = signed_in(dayzie).get(UPCOMING).content.decode()
        assert "Trade offered to you, waiting" in theirs and "Class that day" in theirs
        assert f'data-tour-trade-accept="{booked.pk}">Accept Trade<' in theirs
        assert f'data-tour-trade-decline="{booked.pk}"' in theirs and "data-tour-trade-withdraw" not in theirs
        assert f'data-tour-take="{booked.pk}"' not in theirs  # one open trade at a time

    def it_shows_a_take_as_approve_to_the_tours_guide(guide: Member, dayzie: Member, booked: TourBooking):
        offer_take(booked, taker=dayzie, actor=_user(dayzie))
        own = signed_in(guide).get(UPCOMING).content.decode()
        assert "Dayzie asked to take it, waiting" in own
        assert f'data-tour-trade-accept="{booked.pk}">Approve<' in own
        assert "You asked to take it, waiting" in signed_in(dayzie).get(UPCOMING).content.decode()


def describe_the_trade_modal():
    def it_lists_every_guide_but_the_tours_own(guide: Member, dayzie: Member, admin: Member, booked: TourBooking):
        content = signed_in(guide).get(_url("booking_trade", booked), **HTMX).content.decode()
        assert f'<option value="{dayzie.pk}">Dayzie Dove</option>' in content
        assert f'<option value="{admin.pk}">Ada Admin</option>' in content
        assert f'value="{guide.pk}"' not in content
        assert "data-tour-trade-send" in content and 'name="note"' in content

    def it_names_an_open_trade_and_says_when_the_tour_cannot_be_traded(
        guide: Member, dayzie: Member, booked: TourBooking
    ):
        offer_trade(booked, to=dayzie, actor=_user(guide))
        client = signed_in(guide)
        assert "Already offered to Dayzie Dove." in client.get(_url("booking_trade", booked), **HTMX).content.decode()
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.CANCELLED)
        assert "This tour was cancelled." in client.get(_url("booking_trade", booked), **HTMX).content.decode()

    def it_sends_a_plain_get_to_upcoming_and_403s_another_guide(guide: Member, dayzie: Member, booked: TourBooking):
        response = signed_in(guide).get(_url("booking_trade", booked))
        assert (response.status_code, response["Location"]) == (302, UPCOMING)
        assert signed_in(dayzie).get(_url("booking_trade", booked), **HTMX).status_code == 403
        assert signed_in(dayzie).post(_url("booking_trade", booked), {"to": dayzie.pk}, **HTMX).status_code == 403

    def it_sends_the_trade_with_a_toast(guide: Member, dayzie: Member, booked: TourBooking):
        response = signed_in(guide).post(_url("booking_trade", booked), {"to": dayzie.pk, "note": "Thanks!"}, **HTMX)
        assert (response.status_code, response["HX-Redirect"]) == (204, UPCOMING)
        assert _flashes(response) == ["We offered Sam Rivera's tour to Dayzie. It stays yours until they accept."]
        booked.refresh_from_db()
        assert (booked.guide, booked.trade_to, booked.trade_note) == (guide, dayzie, "Thanks!")

    def it_shows_the_form_again_on_a_bad_post(guide: Member, booked: TourBooking):
        client = signed_in(guide)
        blank = client.post(_url("booking_trade", booked), {"to": ""}, **HTMX).content.decode()
        assert "Pick the guide to trade it to." in blank
        own = client.post(_url("booking_trade", booked), {"to": guide.pk}, **HTMX).content.decode()
        assert "Pick a guide from the list." in own
        long = client.post(_url("booking_trade", booked), {"to": "", "note": "x" * (MAX_TRADE_NOTE + 1)}, **HTMX)
        assert "Ensure this value has at most" in long.content.decode()
        booked.refresh_from_db()
        assert not booked.has_open_trade

    def it_flashes_why_when_the_tour_was_cancelled_meanwhile(guide: Member, dayzie: Member, booked: TourBooking):
        client = signed_in(guide)
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.CANCELLED)
        response = client.post(_url("booking_trade", booked), {"to": dayzie.pk}, **HTMX)
        assert _flashes(response) == ["This tour was cancelled."]


def describe_the_take_answer_and_withdraw_posts():
    def it_asks_to_take_with_a_toast(guide: Member, dayzie: Member, booked: TourBooking):
        response = signed_in(dayzie).post(_url("booking_take", booked), {"note": "Happy to"}, **HTMX)
        assert _flashes(response) == ["We asked Gina for Sam Rivera's tour. It moves to you once they approve."]
        booked.refresh_from_db()
        assert (booked.trade_to, booked.trade_note, booked.is_take) == (dayzie, "Happy to", True)

    def it_flashes_a_refused_take_and_a_note_too_long(guide: Member, dayzie: Member, booked: TourBooking):
        assert _flashes(signed_in(guide).post(_url("booking_take", booked), **HTMX)) == [SAME_GUIDE_MESSAGE]
        long = signed_in(dayzie).post(_url("booking_take", booked), {"note": "x" * (MAX_TRADE_NOTE + 1)}, **HTMX)
        assert _flashes(long) == [
            f"Ensure this value has at most {MAX_TRADE_NOTE} characters (it has {MAX_TRADE_NOTE + 1})."
        ]

    def it_accepts_and_declines_by_post_and_403s_anyone_else_admins_included(
        guide: Member, dayzie: Member, admin: Member, booked: TourBooking
    ):
        key = offer_trade(booked, to=dayzie, actor=_user(guide)).trade_key
        url = _url("booking_trade_answer", booked)
        for member in (guide, admin):
            assert signed_in(member).post(url, {"answer": "accept", "trade": key}, **HTMX).status_code == 403
        assert signed_in(dayzie).post(url, {"answer": "maybe", "trade": key}, **HTMX).status_code == 400
        response = signed_in(dayzie).post(url, {"answer": "accept", "trade": key}, **HTMX)
        assert _flashes(response) == [
            f"Sam Rivera's tour on {short_when(FOUR)} is yours now. We told Sam who their guide is."
        ]
        booked.refresh_from_db()
        assert booked.guide == dayzie
        again = signed_in(dayzie).post(url, {"answer": "accept", "trade": key}, **HTMX)
        assert _flashes(again) == ["This trade was withdrawn."]

    def it_words_each_answer(guide: Member, dayzie: Member, booked: TourBooking):
        url = _url("booking_trade_answer", booked)
        key = offer_trade(booked, to=dayzie, actor=_user(guide)).trade_key
        declined = signed_in(dayzie).post(url, {"answer": "decline", "trade": key}, **HTMX)
        assert _flashes(declined) == [f"You declined Sam Rivera's tour on {short_when(FOUR)}. It stays with Gina."]
        key = offer_take(booked, taker=dayzie, actor=_user(dayzie)).trade_key
        kept = signed_in(guide).post(url, {"answer": "decline", "trade": key}, **HTMX)
        assert _flashes(kept) == [f"Sam Rivera's tour on {short_when(FOUR)} stays yours."]
        key = offer_take(booked, taker=dayzie, actor=_user(dayzie)).trade_key
        approved = signed_in(guide).post(url, {"answer": "accept", "trade": key}, **HTMX)
        assert _flashes(approved) == [
            f"Sam Rivera's tour on {short_when(FOUR)} is Dayzie's now. We told Sam who their guide is."
        ]

    def it_withdraws_by_post_and_403s_anyone_else(guide: Member, dayzie: Member, booked: TourBooking):
        key = offer_trade(booked, to=dayzie, actor=_user(guide)).trade_key
        url = _url("booking_trade_withdraw", booked)
        assert signed_in(dayzie).post(url, {"trade": key}, **HTMX).status_code == 403
        response = signed_in(guide).post(url, {"trade": key}, **HTMX)
        assert _flashes(response) == [f"You withdrew the trade of Sam Rivera's tour on {short_when(FOUR)}."]
        assert _flashes(signed_in(guide).post(url, {"trade": key}, **HTMX)) == ["This trade was withdrawn."]


@pytest.mark.postgres_only
def describe_on_postgresql():
    """The answer and the withdraw lock the booking with its guides joined; PostgreSQL must accept that lock."""

    def it_accepts_and_withdraws_under_the_row_lock(guide: Member, dayzie: Member, booked: TourBooking, committed):
        key = _offer(booked, dayzie, guide, committed)
        withdraw_trade(booked, key=key, asker=guide)
        key = _offer(booked, dayzie, guide, committed)
        with committed():
            answer_trade(booked, accept=True, key=key, answerer=dayzie)
        booked.refresh_from_db()
        assert booked.guide == dayzie
