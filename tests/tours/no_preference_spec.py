"""No preference (#1255): a visitor may leave the guide open when two guides share a time; the first to confirm gets it."""

from __future__ import annotations

import re
import threading
import time as clock
from datetime import UTC, date, time, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours import booking as booking_service
from tours.availability import open_tour_at, open_tours
from tours.booking import (
    TourFullError,
    TourLockedError,
    TourUnavailableError,
    Visitor,
    book_any_guide,
    cancel_tour_by_visitor,
    confirm_tour_request,
    decline_tour_request,
    lapse_request,
)
from tours.models import FacilityTourSettings, TourBooking, TourGuideSettings
from tours.notify import shared_with_label
from tours.picker import NoPreference, build_picker, find_tour, guide_choices, parse_any_guide_key, party_room

pytestmark = pytest.mark.django_db

# 2030-01-07 is a Monday; both guides give tours Tuesday the 8th, 4:00 and 5:00 PM.
NOW = local(date(2030, 1, 7), 9)
TUESDAY = date(2030, 1, 8)
FOUR = local(TUESDAY, 16)
ANY_FOUR = f"any-{FOUR.astimezone(UTC):%Y%m%dT%H%MZ}"
HOST = "tours.pastlives.space"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    cache.clear()


def _visitor(party: int = 2) -> Visitor:
    return Visitor(name="Sam Rivera", email="sam@example.com", phone="", party_size=party)


def _guide(name: str, *, first: bool, seats: int = 4, weekday: int | None = None) -> Member:
    """A Tour Guide with an account, Tuesday hours and the confirm mode asked for."""
    member = make_guide(full_legal_name=name)
    TourHoursFactory(guide=member, seats=seats, **({"weekday": weekday} if weekday is not None else {}))
    if first:
        TourGuideSettings.objects.create(
            guide=member, confirm_mode=TourGuideSettings.ConfirmMode.FIRST, min_notice_hours=25
        )
    signed_in(member)
    member.refresh_from_db()
    return member


def _soon_day() -> date:
    """A day the live pages' clock admits, never a Tuesday (the guides' weekly hours already cover those)."""
    day = timezone.localdate() + timedelta(days=4)
    return day + timedelta(days=1) if day.weekday() == TUESDAY.weekday() else day


def _to(address: str) -> list[Any]:
    return [message for message in mail.outbox if message.to == [address]]


@pytest.fixture
def alex() -> Member:
    return _guide("Alex Able", first=True)


@pytest.fixture
def gina() -> Member:
    return _guide("Gina Guide", first=True)


@pytest.fixture
def shared(alex: Member, gina: Member, django_capture_on_commit_callbacks: Any) -> list[TourBooking]:
    """A No preference request sent to Alex and Gina, both Confirm First; the outbox starts empty."""
    with django_capture_on_commit_callbacks(execute=True):
        book_any_guide(starts_at=FOUR, visitor=_visitor(), now=NOW)
    mail.outbox.clear()
    return list(TourBooking.objects.order_by("pk"))


def _soon_pair(alex: Member, gina: Member, *, gina_seats: int = 4) -> tuple[str, str, str]:
    """Both guides at 4:00 PM on a day the live pages admit: the No preference key, Alex's and Gina's."""
    day = _soon_day()
    TourHoursFactory(guide=alex, weekday=day.weekday(), seats=4)
    TourHoursFactory(guide=gina, weekday=day.weekday(), seats=gina_seats, end_time=time(17, 0))
    stamp = f"{local(day, 16).astimezone(UTC):%Y%m%dT%H%MZ}"
    return f"any-{stamp}", f"{alex.pk}-{stamp}", f"{gina.pk}-{stamp}"


def _options(page: str) -> list[tuple[str, bool, str]]:
    """The Guide dropdown's options as (value, selected, label)."""
    found = re.findall(r'<option value="([^"]+)"( selected)?[^>]*data-tour-guide-option="[^"]+">([^<]+)</option>', page)
    return [(value, bool(selected), label) for value, selected, label in found]


def describe_the_picker():
    def it_shows_one_time_where_guides_share_it_and_names_them_only_on_reschedule(alex: Member):
        gina = make_guide(full_legal_name="Gina Guide")
        TourHoursFactory(guide=gina, seats=2, end_time=time(17, 0))
        tours = open_tours(TUESDAY, TUESDAY, now=NOW, guide_notice=False)
        picker = build_picker(tours, "", no_preference=True)
        labels = [(timezone.localtime(c.tour.starts_at).hour, c.guide_name, c.no_preference) for c in picker.times]
        assert labels == [(16, "", True), (17, "", False)]
        assert picker.times[0].tour.key == ANY_FOUR and picker.times[0].tour.seats_left == 4
        rescheduling = [(c.guide_name, c.no_preference) for c in build_picker(tours, "").times]
        assert rescheduling == [("Alex", False), ("Gina", False), ("", False)]

    def it_offers_no_preference_then_each_guide_only_where_guides_share_the_time(alex: Member):
        gina = make_guide(full_legal_name="Gina Guide", preferred_name="Gee")
        TourHoursFactory(guide=gina, seats=2, end_time=time(17, 0))
        tours = open_tours(TUESDAY, TUESDAY, now=NOW, guide_notice=False)
        guides = guide_choices(tours, find_tour(tours, ANY_FOUR))
        four = FOUR.astimezone(UTC)
        assert [(g.key, g.name, g.seats_left, g.no_preference) for g in guides] == [
            (ANY_FOUR, "No preference", 4, True),
            (f"{alex.pk}-{four:%Y%m%dT%H%MZ}", "Alex", 4, False),
            (f"{gina.pk}-{four:%Y%m%dT%H%MZ}", "Gee", 2, False),
        ]
        gina_four = next(tour for tour in tours if tour.guide == gina)
        assert guide_choices(tours, gina_four) == guides and party_room(guides, gina_four) == 4
        five = next(tour for tour in tours if timezone.localtime(tour.starts_at).hour == 17)
        assert guide_choices(tours, five) == [] and party_room([], five) == 4

    def it_finds_the_choice_by_its_key_only_where_guides_share_the_time(alex: Member, gina: Member):
        tours = open_tours(TUESDAY, TUESDAY, now=NOW, guide_notice=False)
        chosen = find_tour(tours, ANY_FOUR)
        assert isinstance(chosen, NoPreference) and chosen.no_preference and chosen.key == ANY_FOUR
        alone = [tour for tour in tours if tour.guide == alex]
        assert find_tour(alone, ANY_FOUR) is None
        assert parse_any_guide_key("any-nonsense") is None and parse_any_guide_key("12-20300108T0000Z") is None

    def it_shows_one_button_per_time_on_the_booking_page(alex: Member, gina: Member):
        anyone, _, _ = _soon_pair(alex, gina, gina_seats=2)
        page = Client(HTTP_HOST=HOST).get(f"/?date={_soon_day():%Y-%m-%d}").content.decode()
        assert page.count("data-tour-time=") == 2
        assert f'data-tour-time="{anyone}" data-tour-no-preference' in page
        assert re.search(r"4:00 PM</span>\s*<span class=\"pl-tourpub__time-seats\">4 seats left</span>", page)
        assert "seats left with" not in page

    def it_opens_the_form_with_the_guide_dropdown_no_preference_first_and_picked(alex: Member, gina: Member):
        anyone, at_alex, at_gina = _soon_pair(alex, gina, gina_seats=2)
        form = Client(HTTP_HOST=HOST).get(f"/?tour={anyone}").content.decode()
        assert 'name="tour" id="tour-guide"' in form and 'type="hidden" name="tour"' not in form
        assert _options(form) == [
            (anyone, True, "No preference"),
            (at_alex, False, "Alex (4 seats left)"),
            (at_gina, False, "Gina (2 seats left)"),
        ]
        assert '<option value="4">4</option>' in form and '<option value="5">' not in form

    def it_keeps_a_picked_guide_and_still_offers_the_roomiest_party(alex: Member, gina: Member):
        _, _, at_gina = _soon_pair(alex, gina, gina_seats=2)
        form = Client(HTTP_HOST=HOST).get(f"/?tour={at_gina}").content.decode()
        assert [selected for _, selected, _ in _options(form)] == [False, False, True]
        assert '<option value="4">4</option>' in form

    def it_shows_no_dropdown_for_a_time_one_guide_gives(alex: Member, gina: Member):
        _soon_pair(alex, gina, gina_seats=2)
        five = f"{alex.pk}-{local(_soon_day(), 17).astimezone(UTC):%Y%m%dT%H%MZ}"
        form = Client(HTTP_HOST=HOST).get(f"/?tour={five}").content.decode()
        assert "data-tour-form" in form and "data-tour-guide" not in form
        assert f'type="hidden" name="tour" value="{five}"' in form

    def it_keeps_the_dropdown_and_the_pick_on_a_form_sent_back(alex: Member, gina: Member):
        _, _, at_gina = _soon_pair(alex, gina, gina_seats=2)
        data = {
            "tour": at_gina,
            "name": "Sam Rivera",
            "email": "not an email",
            "phone": "503 555 0142",
            "party_size": "1",
        }
        page = Client(HTTP_HOST=HOST).post("/", data).content.decode()
        assert [selected for _, selected, _ in _options(page)] == [False, False, True]
        assert '<option value="4">4</option>' in page and not TourBooking.objects.exists()


def describe_booking_from_the_guide_dropdown():
    def _post(key: str, party: int = 2) -> Any:
        data = {
            "tour": key,
            "name": "Sam Rivera",
            "email": "sam@example.com",
            "phone": "503 555 0142",
            "party_size": str(party),
        }
        return Client(HTTP_HOST=HOST).post("/", data)

    def it_books_the_guide_picked_as_their_own_button_did(alex: Member):
        gina = _guide("Gina Guide", first=False)
        _, _, at_gina = _soon_pair(alex, gina)
        assert _post(at_gina).status_code == 302
        booking = TourBooking.objects.get()
        assert (booking.guide, booking.status, booking.request_group) == (gina, TourBooking.Status.BOOKED, None)

    def it_sends_a_confirm_first_guide_picked_a_request_of_their_own(alex: Member, gina: Member):
        _, at_alex, _ = _soon_pair(alex, gina)
        assert _post(at_alex).status_code == 302
        booking = TourBooking.objects.get()
        assert (booking.guide, booking.status, booking.request_group) == (alex, TourBooking.Status.PENDING, None)

    def it_books_no_preference_with_a_guide_who_confirms_automatically(alex: Member):
        gina = _guide("Gina Guide", first=False)
        anyone, _, _ = _soon_pair(alex, gina)
        assert _post(anyone).status_code == 302
        assert TourBooking.objects.get().guide == gina

    def it_refuses_a_guide_without_room_for_the_party(alex: Member, gina: Member):
        _, _, at_gina = _soon_pair(alex, gina, gina_seats=2)
        page = _post(at_gina, party=3).content.decode()
        assert "Only 2 seats are left at that time." in page and not TourBooking.objects.exists()


def describe_booking_with_no_preference():
    def it_books_at_once_with_a_guide_who_confirms_automatically(alex: Member):
        gina = _guide("Gina Guide", first=False)
        booking = book_any_guide(starts_at=FOUR, visitor=_visitor(), now=NOW)
        assert (booking.guide, booking.status, booking.request_group) == (gina, TourBooking.Status.BOOKED, None)
        assert TourBooking.objects.count() == 1

    def it_tries_the_next_automatic_guide_when_the_first_fills_meanwhile(alex: Member, gina: Member):
        TourGuideSettings.objects.all().delete()
        real = booking_service.book_tour

        def alex_filled(*, guide: Member, **kwargs: Any) -> TourBooking:
            if guide == alex:
                raise TourFullError(0)
            return real(guide=guide, **kwargs)

        with patch.object(booking_service, "book_tour", alex_filled):
            booking = book_any_guide(starts_at=FOUR, visitor=_visitor(), now=NOW)
            assert booking.guide == gina
            with patch.object(booking_service, "open_tour_at", return_value=None), pytest.raises(TourUnavailableError):
                book_any_guide(starts_at=FOUR, visitor=_visitor(1), now=NOW)

    def it_asks_the_guides_who_confirm_first_when_the_automatic_one_fills_meanwhile(alex: Member, gina: Member):
        TourGuideSettings.objects.filter(guide=gina).delete()

        def gina_filled(*, guide: Member, **kwargs: Any) -> TourBooking:
            raise TourFullError(0)

        with patch.object(booking_service, "book_tour", gina_filled):
            booking = book_any_guide(starts_at=FOUR, visitor=_visitor(), now=NOW)
        assert (booking.guide, booking.status, booking.request_group) == (alex, TourBooking.Status.PENDING, None)

    def it_books_at_once_at_the_kiosk_with_the_first_guide_listed(alex: Member, gina: Member):
        booking = book_any_guide(starts_at=FOUR, visitor=_visitor(), source=TourBooking.Source.KIOSK, now=NOW)
        assert (booking.guide, booking.status) == (alex, TourBooking.Status.BOOKED)

    def it_sends_an_ordinary_request_when_one_guide_has_room(alex: Member, gina: Member):
        TourBookingFactory(guide=gina, starts_at=FOUR, party_size=3)
        booking = book_any_guide(starts_at=FOUR, visitor=_visitor(2), now=NOW)
        assert (booking.guide, booking.status, booking.request_group) == (alex, TourBooking.Status.PENDING, None)

    def it_requests_every_guide_and_tells_the_visitor_once(
        alex: Member, gina: Member, django_capture_on_commit_callbacks: Any
    ):
        with django_capture_on_commit_callbacks(execute=True):
            booking = book_any_guide(starts_at=FOUR, visitor=_visitor(3), now=NOW)
        rows = list(TourBooking.objects.order_by("pk"))
        assert [row.guide for row in rows] == [alex, gina] and booking == rows[0]
        assert {row.status for row in rows} == {TourBooking.Status.PENDING}
        assert rows[0].request_group is not None and rows[0].request_group == rows[1].request_group
        assert [open_tour_at(member, FOUR, now=NOW, guide_notice=False).seats_left for member in (alex, gina)] == [1, 1]
        (visitor,) = _to("sam@example.com")
        assert "We sent it to every guide" in visitor.body and "as your guide" not in visitor.body
        assert rows[0].self_serve_token in visitor.body
        (to_alex,) = _to(alex.user.email)
        assert "we also sent it to Gina. Whoever confirms first gets the tour." in to_alex.body
        bell = Notification.objects.get(user=gina.user)
        assert bell.body.endswith("Also sent to Alex: whoever confirms first gets it.")

    def it_leaves_out_a_guide_without_room(alex: Member, gina: Member):
        third = _guide("Rae Third", first=True)
        TourBookingFactory(guide=gina, starts_at=FOUR, party_size=3)
        book_any_guide(starts_at=FOUR, visitor=_visitor(2), now=NOW)
        assert sorted(row.guide.pk for row in TourBooking.objects.filter(status="pending")) == [alex.pk, third.pk]

    def it_refuses_a_time_nobody_offers_or_with_no_room(alex: Member, gina: Member):
        with pytest.raises(TourUnavailableError):
            book_any_guide(starts_at=FOUR + timedelta(minutes=30), visitor=_visitor(), now=NOW)
        with pytest.raises(TourFullError) as full:
            book_any_guide(starts_at=FOUR, visitor=_visitor(5), now=NOW)
        assert full.value.seats_left == 4

    def it_counts_again_under_the_lock(alex: Member, gina: Member):
        real = open_tour_at

        def gina_filled(guide: Member, *args: Any, **kwargs: Any) -> Any:
            return None if guide == gina else real(guide, *args, **kwargs)

        with patch.object(booking_service, "open_tour_at", gina_filled):
            booking = book_any_guide(starts_at=FOUR, visitor=_visitor(), now=NOW)
        assert (booking.guide, booking.request_group) == (alex, None)
        with patch.object(booking_service, "open_tour_at", return_value=None), pytest.raises(TourFullError):
            book_any_guide(starts_at=FOUR, visitor=_visitor(), now=NOW)

    def it_links_every_row_to_a_new_account(alex: Member, gina: Member):
        book_any_guide(starts_at=FOUR, visitor=_visitor(), create_account=True, now=NOW)
        users = {row.user for row in TourBooking.objects.all()}
        assert len(users) == 1 and next(iter(users)).email == "sam@example.com"


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
        assert open_tour_at(alex, FOUR, now=NOW, guide_notice=False).seats_left == 4
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
        assert open_tour_at(gina, FOUR, now=NOW, guide_notice=False).seats_left == 4


def describe_the_pages():
    @pytest.fixture
    def soon(alex: Member, gina: Member) -> list[TourBooking]:
        """A shared request four days out, so the live pages' clock admits it."""
        day = _soon_day()
        for member in (alex, gina):
            TourHoursFactory(guide=member, weekday=day.weekday(), seats=4)
        FacilityTourSettings.load()
        key = f"any-{local(day, 16).astimezone(UTC):%Y%m%dT%H%MZ}"
        data = {
            "tour": key,
            "name": "Sam Rivera",
            "email": "sam@example.com",
            "phone": "503 555 0142",
            "party_size": "2",
        }
        response = Client(HTTP_HOST=HOST).post("/", data)
        assert response.status_code == 302
        return list(TourBooking.objects.order_by("pk"))

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
        alex = _guide("Alex Able", first=True)
        gina = _guide("Gina Guide", first=True)
        FacilityTourSettings.load()
        book_any_guide(starts_at=FOUR, visitor=_visitor(), now=NOW)
        rows = list(TourBooking.objects.order_by("pk"))
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
