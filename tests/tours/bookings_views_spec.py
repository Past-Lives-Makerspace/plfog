"""The Tours page's Upcoming and Past tabs (#949): who sees what, and the staff reschedule, cancel and marks."""

from __future__ import annotations

import json
from datetime import UTC, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import UserProfile
from membership.models import Member
from tests.membership.factories import MemberFactory
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.forms import MAX_STAFF_MESSAGE
from tours.models import TourBooking
from tours.notify import short_when

pytestmark = pytest.mark.django_db

UPCOMING = reverse("tours:upcoming")
PAST = reverse("tours:past")
HTMX = {"HTTP_HX_REQUEST": "true"}
DAY = timezone.localdate() + timedelta(days=3)
FOUR = local(DAY, 16)
FIVE = local(DAY, 17)


def _reschedule_url(booking: TourBooking) -> str:
    return reverse("tours:booking_reschedule", args=[booking.pk])


def _cancel_url(booking: TourBooking) -> str:
    return reverse("tours:booking_cancel", args=[booking.pk])


def _mark_url(booking: TourBooking) -> str:
    return reverse("tours:booking_mark", args=[booking.pk])


def _tour_key(guide: Member, starts_at: Any) -> str:
    return f"{guide.pk}-{starts_at.astimezone(UTC):%Y%m%dT%H%MZ}"


def _flashes(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _hours(guide: Member, **kwargs: Any) -> Any:
    return TourHoursFactory(guide=guide, weekday=DAY.weekday(), **kwargs)


@pytest.fixture
def booked(guide: Member) -> TourBooking:
    _hours(guide, seats=4)
    return TourBookingFactory(
        guide=guide,
        starts_at=FOUR,
        party_size=2,
        visitor_name="Sam Rivera",
        visitor_email="sam@example.com",
        visitor_phone="+1 (503) 555-0100",
    )


@pytest.fixture
def other() -> Member:
    return make_guide(full_legal_name="Olive Other")


def _past(guide: Member | None, days: int, **kwargs: Any) -> TourBooking:
    starts = timezone.now() - timedelta(days=days)
    return TourBookingFactory(guide=guide, starts_at=starts, ends_at=starts + timedelta(hours=1), **kwargs)


def describe_access():
    def it_sends_a_plain_member_to_give_tours_and_refuses_every_action(plain: Member, booked: TourBooking):
        client = signed_in(plain)
        give = reverse("tours:give")
        assert client.get(UPCOMING)["Location"] == give
        assert client.get(PAST)["Location"] == give
        assert client.get(_reschedule_url(booked), **HTMX)["HX-Redirect"] == give
        assert client.post(_reschedule_url(booked), {"tour": "x"}).status_code == 403
        assert client.post(_cancel_url(booked)).status_code == 403
        assert client.post(_mark_url(booked), {"outcome": "attended"}).status_code == 403
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.BOOKED

    def it_refuses_a_guide_every_action_on_another_guides_tour(guide: Member, other: Member, booked: TourBooking):
        client = signed_in(other)
        _hours(other)
        assert client.get(_reschedule_url(booked), **HTMX).status_code == 403
        moved = client.post(_reschedule_url(booked), {"tour": _tour_key(other, FIVE)}, **HTMX)
        assert moved.status_code == 403
        assert client.post(_cancel_url(booked), {"message": "x"}, **HTMX).status_code == 403
        past = _past(guide, 1)
        assert client.post(_mark_url(past), {"outcome": "attended"}, **HTMX).status_code == 403
        booked.refresh_from_db()
        past.refresh_from_db()
        assert (booked.status, booked.guide, past.status) == (
            TourBooking.Status.BOOKED,
            guide,
            TourBooking.Status.BOOKED,
        )

    def it_lets_a_guide_filter_by_any_guide(guide: Member, other: Member):
        client = signed_in(guide)
        assert client.get(UPCOMING, {"guide": other.pk}).status_code == 200
        assert client.get(PAST, {"guide": other.pk}).status_code == 200
        assert client.get(UPCOMING, {"guide": guide.pk}).status_code == 200

    def it_404s_an_admins_unknown_guide(admin: Member):
        client = signed_in(admin)
        assert client.get(UPCOMING, {"guide": "abc"}).status_code == 404
        assert client.get(UPCOMING, {"guide": "999999"}).status_code == 404

    def it_404s_an_unknown_booking(guide: Member):
        assert signed_in(guide).post(reverse("tours:booking_cancel", args=[999999])).status_code == 404


def describe_upcoming():
    def it_lists_every_guides_booked_tours_soonest_first(guide: Member, other: Member, booked: TourBooking):
        later = TourBookingFactory(guide=guide, starts_at=FIVE, visitor_name="Later Larry")
        theirs = TourBookingFactory(guide=other, starts_at=FOUR, visitor_name="Olive's Visitor")
        TourBookingFactory(guide=guide, starts_at=FIVE, status=TourBooking.Status.CANCELLED, visitor_name="Gone Gwen")
        _past(guide, 1, visitor_name="Past Pete")
        content = signed_in(guide).get(UPCOMING).content.decode()
        assert content.index(f'data-tour-booking="{booked.pk}"') < content.index(f'data-tour-booking="{later.pk}"')
        assert f'data-tour-booking="{theirs.pk}"' in content
        for absent in ("Gone Gwen", "Past Pete"):
            assert absent not in content
        assert '<option value="" selected>All guides</option>' in content
        assert f'<option value="{guide.pk}">My Tours</option>' in content
        assert 'href="mailto:sam@example.com"' in content
        assert 'href="tel:+15035550100"' in content
        assert "Booked at the kiosk" not in content
        assert f'data-tour-reschedule="{booked.pk}"' in content
        assert f"tour-cancel-{booked.pk}" in content
        assert 'aria-current="page" data-tours-tab="upcoming"' in content

    def it_shows_an_admin_every_guide_and_filters_by_one(
        admin: Member, guide: Member, other: Member, booked: TourBooking
    ):
        theirs = TourBookingFactory(guide=other, starts_at=FOUR, visitor_name="Olive's Visitor")
        unassigned = TourBookingFactory(guide=None, starts_at=FIVE, visitor_name="Nobody's Visitor")
        client = signed_in(admin)
        everything = client.get(UPCOMING).content.decode()
        for booking in (booked, theirs, unassigned):
            assert f'data-tour-booking="{booking.pk}"' in everything
        assert "data-tour-booking-guide>No guide<" in everything
        assert f'<option value="{other.pk}">Olive Other</option>' in everything
        filtered = client.get(UPCOMING, {"guide": other.pk}).content.decode()
        assert f'data-tour-booking="{theirs.pk}"' in filtered
        assert f'data-tour-booking="{booked.pk}"' not in filtered
        assert f'<option value="{other.pk}" selected>Olive Other</option>' in filtered

    def it_says_so_when_nothing_is_coming_up(guide: Member, admin: Member, other: Member):
        content = signed_in(guide).get(UPCOMING).content.decode()
        assert 'data-tours-empty="upcoming"' in content
        assert "Visitors book the times in each guide's tour hours" in content
        assert "data-tours-clear" not in content
        filtered = signed_in(admin).get(UPCOMING, {"guide": other.pk}).content.decode()
        assert "No tours coming up for Olive Other." in filtered
        assert "data-tours-clear" in filtered

    def it_pages_the_list(guide: Member):
        for minute in range(26):
            TourBookingFactory(guide=guide, starts_at=FOUR + timedelta(minutes=minute))
        client = signed_in(guide)
        first = client.get(UPCOMING).content.decode()
        assert first.count("data-tour-booking=") == 25
        assert "Page 1 of 2" in first
        assert client.get(UPCOMING, {"page": 2}).content.decode().count("data-tour-booking=") == 1

    def it_keeps_the_admins_guide_filter_on_the_page_links(admin: Member, guide: Member):
        for minute in range(26):
            TourBookingFactory(guide=guide, starts_at=FOUR + timedelta(minutes=minute))
        content = signed_in(admin).get(UPCOMING, {"guide": guide.pk}).content.decode()
        assert f"?page=2&amp;guide={guide.pk}" in content or f"?page=2&guide={guide.pk}" in content


def describe_past():
    def it_lists_started_tours_newest_first_without_cancelled_ones(guide: Member, booked: TourBooking):
        older = _past(guide, 5, visitor_name="Older Olga", status=TourBooking.Status.ATTENDED)
        newer = _past(guide, 1, visitor_name="Newer Ned")
        _past(guide, 2, visitor_name="Cancelled Cal", status=TourBooking.Status.CANCELLED)
        content = signed_in(guide).get(PAST).content.decode()
        assert content.index(f'data-tour-booking="{newer.pk}"') < content.index(f'data-tour-booking="{older.pk}"')
        assert "Cancelled Cal" not in content and f'data-tour-booking="{booked.pk}"' not in content
        assert 'data-tour-booking-status="booked">Not Marked' in content
        assert 'data-tour-booking-status="attended">Attended' in content
        assert f'data-tour-booking="{older.pk}"' in content
        assert 'value="attended" class="pl-row-menu__item" role="menuitem" aria-pressed="true"' in content

    def it_says_so_when_no_tour_has_happened(guide: Member):
        content = signed_in(guide).get(PAST).content.decode()
        assert 'data-tours-empty="past"' in content
        assert "No past tours yet." in content
        assert 'id="tour-reschedule"' not in content


def describe_reschedule_modal():
    def it_sends_a_plain_get_to_the_upcoming_tab(guide: Member, booked: TourBooking):
        response = signed_in(guide).get(_reschedule_url(booked))
        assert (response.status_code, response["Location"]) == (302, UPCOMING)

    def it_offers_the_guides_open_times_with_room_but_not_the_booking_own(guide: Member, booked: TourBooking):
        content = signed_in(guide).get(_reschedule_url(booked), **HTMX).content.decode()
        assert f'data-tour-move-time="{_tour_key(guide, FIVE)}"' in content
        assert f'data-tour-move-time="{_tour_key(guide, FOUR)}"' not in content
        assert f'<option value="{guide.pk}" selected>Gina Guide</option>' in content
        assert "Sam Rivera, 2 people." in content

    def it_leaves_out_a_time_without_room_for_the_party(guide: Member, booked: TourBooking):
        TourBookingFactory(guide=guide, starts_at=FIVE, party_size=3)
        client = signed_in(guide)
        content = client.get(_reschedule_url(booked), {"date": DAY.isoformat()}, **HTMX).content.decode()
        assert f'data-tour-move-time="{_tour_key(guide, FIVE)}"' not in content
        TourBooking.objects.filter(pk=booked.pk).update(party_size=5)
        assert (
            "No other open times have room for this party."
            in client.get(_reschedule_url(booked), **HTMX).content.decode()
        )

    def it_picks_a_day_and_shows_the_confirm_for_a_time(guide: Member, booked: TourBooking):
        client = signed_in(guide)
        day = client.get(_reschedule_url(booked), {"date": DAY.isoformat()}, **HTMX).content.decode()
        assert f'data-tour-move-day="{DAY.isoformat()}"' in day
        chosen = client.get(_reschedule_url(booked), {"tour": _tour_key(guide, FIVE)}, **HTMX).content.decode()
        assert f'data-tour-move-chosen="{_tour_key(guide, FIVE)}"' in chosen
        assert 'name="message"' in chosen and "data-tour-move-save" in chosen
        unknown = client.get(_reschedule_url(booked), {"tour": "nope"}, **HTMX).content.decode()
        assert "data-tour-move-chosen" not in unknown and "data-tour-move-time" in unknown

    def it_lets_an_admin_pick_another_guides_times(admin: Member, guide: Member, other: Member, booked: TourBooking):
        _hours(other)
        content = signed_in(admin).get(_reschedule_url(booked), {"guide": other.pk}, **HTMX).content.decode()
        assert f'data-tour-move-time="{_tour_key(other, FOUR)}"' in content
        assert f'<option value="{other.pk}" selected>Olive Other</option>' in content
        assert f'<option value="{guide.pk}">Gina Guide</option>' in content
        chosen = signed_in(admin).get(
            _reschedule_url(booked), {"guide": other.pk, "tour": _tour_key(other, FOUR)}, **HTMX
        )
        assert "with Olive Other" in chosen.content.decode()

    def it_says_when_the_picked_guide_has_no_room(admin: Member, other: Member, booked: TourBooking):
        content = signed_in(admin).get(_reschedule_url(booked), {"guide": other.pk}, **HTMX).content.decode()
        assert "Olive Other has no open times with room for this party." in content
        assert "or pick another guide" in content

    def it_lets_a_guide_hand_their_tour_to_another_guide_and_404s_a_non_guide(
        guide: Member, other: Member, admin: Member, plain: Member, booked: TourBooking
    ):
        _hours(other)
        content = signed_in(guide).get(_reschedule_url(booked), {"guide": other.pk}, **HTMX).content.decode()
        assert f'data-tour-move-time="{_tour_key(other, FOUR)}"' in content
        assert signed_in(guide).get(_reschedule_url(booked), {"guide": plain.pk}, **HTMX).status_code == 404
        client = signed_in(admin)
        assert client.get(_reschedule_url(booked), {"guide": plain.pk}, **HTMX).status_code == 404
        assert client.get(_reschedule_url(booked), {"guide": "abc"}, **HTMX).status_code == 404

    def it_starts_an_unassigned_tour_from_the_admins_own_times(admin: Member, booked: TourBooking):
        _hours(admin)
        TourBooking.objects.filter(pk=booked.pk).update(guide=None)
        content = signed_in(admin).get(_reschedule_url(booked), **HTMX).content.decode()
        assert f'data-tour-move-time="{_tour_key(admin, FOUR)}"' in content


def describe_reschedule_post():
    def it_asks_the_visitor_first_and_sends_the_page_back_with_a_flash(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        client = signed_in(guide)
        current = f"http://testserver{UPCOMING}?page=1"
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                _reschedule_url(booked),
                {"tour": _tour_key(guide, FIVE), "message": "See you at five"},
                HTTP_HX_CURRENT_URL=current,
                **HTMX,
            )
        assert (response.status_code, response["HX-Redirect"]) == (204, f"{UPCOMING}?page=1")
        booked.refresh_from_db()
        assert (booked.starts_at, booked.sequence, booked.proposed_starts_at) == (FOUR, 0, FIVE)
        assert _flashes(response) == [
            f"We asked Sam Rivera to move to {short_when(FIVE)}. "
            "Their tour stays put until they accept, and you'll hear their answer."
        ]
        visitor = [message for message in mail.outbox if message.to == ["sam@example.com"]]
        assert len(visitor) == 1 and "See you at five" in visitor[0].body
        assert visitor[0].subject.startswith("Can your Past Lives tour move to")

    def it_redirects_a_plain_post(guide: Member, booked: TourBooking):
        response = signed_in(guide).post(_reschedule_url(booked), {"tour": _tour_key(guide, FIVE)})
        assert (response.status_code, response["Location"]) == (302, UPCOMING)

    def it_lets_an_admin_propose_another_guides_tour(admin: Member, guide: Member, other: Member, booked: TourBooking):
        _hours(other)
        response = signed_in(admin).post(_reschedule_url(booked), {"tour": _tour_key(other, FOUR)}, **HTMX)
        assert response.status_code == 204
        booked.refresh_from_db()
        assert (booked.guide, booked.proposed_guide) == (guide, other)

    def it_proposes_inside_the_cancel_cutoff(guide: Member, booked: TourBooking):
        client = signed_in(guide)
        with patch("django.utils.timezone.now", return_value=FOUR - timedelta(hours=2)):
            response = client.post(_reschedule_url(booked), {"tour": _tour_key(guide, FIVE)}, **HTMX)
        assert response.status_code == 204
        booked.refresh_from_db()
        assert (booked.starts_at, booked.proposed_starts_at) == (FOUR, FIVE)

    def it_shows_the_modal_again_with_why(guide: Member, booked: TourBooking):
        client = signed_in(guide)
        gone = client.post(_reschedule_url(booked), {"tour": "garbage"}, **HTMX).content.decode()
        assert "That time is no longer open, pick another." in gone
        unknown_guide = client.post(_reschedule_url(booked), {"tour": "999999-20300101T0000Z"}, **HTMX)
        assert "That time is no longer open" in unknown_guide.content.decode()
        TourBookingFactory(guide=guide, starts_at=FIVE, party_size=3)
        full = client.post(_reschedule_url(booked), {"tour": _tour_key(guide, FIVE)}, **HTMX).content.decode()
        assert "Only 1 seat is left at that time." in full and "data-tour-move-error" in full
        booked.refresh_from_db()
        assert booked.starts_at == FOUR

    def it_refuses_a_message_too_long(guide: Member, booked: TourBooking):
        long = "x" * (MAX_STAFF_MESSAGE + 1)
        content = (
            signed_in(guide)
            .post(_reschedule_url(booked), {"tour": _tour_key(guide, FIVE), "message": long}, **HTMX)
            .content.decode()
        )
        assert "Ensure this value has at most" in content
        assert f'data-tour-move-chosen="{_tour_key(guide, FIVE)}"' in content
        booked.refresh_from_db()
        assert booked.starts_at == FOUR

    def it_flashes_why_when_the_tour_was_cancelled_meanwhile(guide: Member, booked: TourBooking):
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.CANCELLED)
        response = signed_in(guide).post(_reschedule_url(booked), {"tour": _tour_key(guide, FIVE)}, **HTMX)
        assert response.status_code == 204
        assert _flashes(response) == ["This tour was already cancelled."]


def describe_cancel():
    def it_cancels_with_the_message_and_frees_the_seats(
        guide: Member, booked: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        client = signed_in(guide)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(_cancel_url(booked), {"message": "Sorry, I am sick."}, **HTMX)
        assert (response.status_code, response["HX-Redirect"]) == (204, UPCOMING)
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.CANCELLED
        assert booked.cancelled_by == guide.user
        assert _flashes(response) == [f"Sam Rivera's tour on {short_when(FOUR)} is cancelled. We emailed them."]
        visitor = [message for message in mail.outbox if message.to == ["sam@example.com"]]
        assert len(visitor) == 1 and "Sorry, I am sick." in visitor[0].body

    def it_redirects_a_plain_post(guide: Member, booked: TourBooking):
        response = signed_in(guide).post(_cancel_url(booked))
        assert (response.status_code, response["Location"]) == (302, UPCOMING)

    def it_lets_an_admin_cancel_any_guides_tour(admin: Member, booked: TourBooking):
        assert signed_in(admin).post(_cancel_url(booked), **HTMX).status_code == 204
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.CANCELLED

    def it_flashes_why_it_refused(guide: Member, booked: TourBooking):
        client = signed_in(guide)
        too_long = client.post(_cancel_url(booked), {"message": "x" * (MAX_STAFF_MESSAGE + 1)}, **HTMX)
        assert _flashes(too_long)[0].startswith("Ensure this value has at most")
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.BOOKED
        started = _past(guide, 1)
        assert _flashes(client.post(_cancel_url(started), **HTMX))[-1:] == [
            "This tour has already started, so it can no longer be moved or cancelled."
        ]


def describe_mark():
    def it_marks_attended_over_htmx_with_a_toast_and_stamps_the_visitor(guide: Member):
        visitor = MemberFactory(full_legal_name="Vera Visitor")
        signed_in(visitor)
        visitor.refresh_from_db()
        past = _past(guide, 1, visitor_name="Vera Visitor", user=visitor.user)
        response = signed_in(guide).post(_mark_url(past), {"outcome": "attended"}, **HTMX)
        assert response.status_code == 200
        assert json.loads(response["HX-Trigger"]) == {
            "showToast": {"message": "Vera Visitor marked attended.", "type": "success"}
        }
        content = response.content.decode()
        assert content.strip().startswith("<tr") and 'data-tour-booking-status="attended"' in content
        past.refresh_from_db()
        assert past.status == TourBooking.Status.ATTENDED
        assert UserProfile.objects.get(user=visitor.user).completed_tour_at == past.starts_at

    def it_changes_to_no_show(guide: Member):
        past = _past(guide, 1, visitor_name="Ned", status=TourBooking.Status.ATTENDED)
        response = signed_in(guide).post(_mark_url(past), {"outcome": "no_show"}, **HTMX)
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "Ned marked a no show."
        past.refresh_from_db()
        assert past.status == TourBooking.Status.NO_SHOW

    def it_redirects_a_plain_post_to_the_past_tab(guide: Member):
        past = _past(guide, 1, visitor_name="Pia")
        response = signed_in(guide).post(_mark_url(past), {"outcome": "attended"})
        assert (response.status_code, response["Location"]) == (302, PAST)
        assert _flashes(response) == ["Pia marked attended."]

    def it_refuses_an_unknown_outcome(guide: Member):
        past = _past(guide, 1)
        assert signed_in(guide).post(_mark_url(past), {"outcome": "booked"}).status_code == 400

    def it_toasts_why_it_refused_a_tour_that_has_not_started(guide: Member, booked: TourBooking):
        response = signed_in(guide).post(_mark_url(booked), {"outcome": "attended"}, **HTMX)
        assert json.loads(response["HX-Trigger"])["showToast"] == {
            "message": "This tour has not started yet. Mark it after it starts.",
            "type": "error",
        }
        booked.refresh_from_db()
        assert booked.status == TourBooking.Status.BOOKED

    def it_flashes_a_refusal_on_a_plain_post(guide: Member, booked: TourBooking):
        response = signed_in(guide).post(_mark_url(booked), {"outcome": "attended"})
        assert response.status_code == 302
        assert _flashes(response) == ["This tour has not started yet. Mark it after it starts."]

    def it_lets_an_admin_mark_any_guides_tour(admin: Member, guide: Member):
        past = _past(guide, 1)
        assert signed_in(admin).post(_mark_url(past), {"outcome": "no_show"}, **HTMX).status_code == 200


def describe_back_url():
    def it_returns_only_to_a_tours_page_on_this_site(guide: Member, booked: TourBooking):
        client = signed_in(guide)
        elsewhere = client.post(_cancel_url(booked), HTTP_HX_CURRENT_URL="https://evil.example/facility-tours/", **HTMX)
        assert elsewhere["HX-Redirect"] == UPCOMING
        other_page = _past(guide, 1)
        response = client.post(
            _mark_url(other_page), {"outcome": "attended"}, HTTP_HX_CURRENT_URL="http://testserver/members/"
        )
        assert response["Location"] == PAST
        past_page = client.post(
            _cancel_url(TourBookingFactory(guide=guide, starts_at=FIVE)),
            HTTP_HX_CURRENT_URL=f"http://testserver{PAST}",
            **HTMX,
        )
        assert past_page["HX-Redirect"] == PAST


def describe_where_it_lives():
    def it_points_the_sidebar_entry_and_the_guide_notices_at_upcoming(guide: Member):
        client: Client = signed_in(guide)
        assert (
            f'href="{UPCOMING}" class="hub-sidebar__link active" data-nav="tours"'
            in client.get(UPCOMING).content.decode()
        )
        tabs = client.get(UPCOMING).content.decode()
        for tab in ("upcoming", "past", "hours"):
            assert f'data-tours-tab="{tab}"' in tabs
        assert 'data-tours-tab="settings"' not in tabs


def describe_review_fixes():
    def it_shows_why_a_cancelled_or_started_tour_cannot_move(guide: Member, booked: TourBooking):
        client = signed_in(guide)
        TourBooking.objects.filter(pk=booked.pk).update(status=TourBooking.Status.CANCELLED)
        cancelled = client.get(_reschedule_url(booked), **HTMX).content.decode()
        assert f'data-tour-move-locked="{booked.pk}"' in cancelled
        assert "This tour was already cancelled." in cancelled and "data-tour-move-time" not in cancelled
        started = _past(guide, 1)
        content = client.get(_reschedule_url(started), **HTMX).content.decode()
        assert "This tour has already started" in content

    def it_says_the_time_is_gone_when_a_refused_message_meets_a_full_tour(guide: Member, booked: TourBooking):
        TourBookingFactory(guide=guide, starts_at=FIVE, party_size=4)
        content = (
            signed_in(guide)
            .post(
                _reschedule_url(booked),
                {"tour": _tour_key(guide, FIVE), "message": "x" * (MAX_STAFF_MESSAGE + 1)},
                **HTMX,
            )
            .content.decode()
        )
        assert "That time is no longer open, pick another." in content
        assert "data-tour-move-chosen" not in content

    def it_names_the_cancelled_time_from_the_locked_row(guide: Member, booked: TourBooking):
        moved = TourBooking.objects.get(pk=booked.pk)
        moved.starts_at = FIVE
        with patch("tours.views.cancel_tour_by_staff", return_value=moved):
            response = signed_in(guide).post(_cancel_url(booked), **HTMX)
        assert _flashes(response) == [f"Sam Rivera's tour on {short_when(FIVE)} is cancelled. We emailed them."]


def describe_the_admins_guide_picker_scenario():
    """The e2e's admin reassignment (#949): the server offers Olive's 4:00 once Olive is picked.

    The flaky e2e showed Gina's own picker (her 4:00 left out as Sam's own tour, her 5:00 with 4
    seats) under a select reading Olive: the change was fired before htmx had wired up the swapped
    in select, so no request went out. These pin both renders, so that screenshot can only mean
    the request never left the browser, never a wrong answer from here.
    """

    def it_offers_olives_four_with_room_and_gina_only_her_five(admin: Member, guide: Member, other: Member):
        _hours(guide, seats=4)
        _hours(other, seats=4)
        sam = TourBookingFactory(guide=guide, starts_at=FOUR, party_size=2, visitor_name="Sam Rivera")
        TourBookingFactory(guide=other, starts_at=FOUR, party_size=1, visitor_name="Oli Visitor")
        client = signed_in(admin)
        olive = client.get(_reschedule_url(sam), {"guide": other.pk}, **HTMX).content.decode()
        assert f'data-tour-move-time="{_tour_key(other, FOUR)}"' in olive
        assert f'data-tour-move-time="{_tour_key(other, FIVE)}"' in olive
        assert "3 seats left" in olive
        assert f'<option value="{other.pk}" selected>Olive Other</option>' in olive
        gina = client.get(_reschedule_url(sam), **HTMX).content.decode()
        assert f'data-tour-move-time="{_tour_key(guide, FOUR)}"' not in gina
        assert f'data-tour-move-time="{_tour_key(guide, FIVE)}"' in gina
        assert f'<option value="{guide.pk}" selected>Gina Guide</option>' in gina
