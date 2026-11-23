"""The booking page's step bar (Morlock, 2026-11-22): Day & Time, Your Details, Booked, the current one
highlighted, an obvious Back from the details, and what the visitor typed kept in their browser tab
(never at the lobby kiosk, a shared screen)."""

from __future__ import annotations

from datetime import UTC, date, timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import FacilityTourSettings

pytestmark = pytest.mark.django_db

HOST = "tours.pastlives.space"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    cache.clear()


def _day() -> date:
    return timezone.localdate() + timedelta(days=4)


def _key(guide: Member) -> str:
    return f"{guide.pk}-{local(_day(), 16).astimezone(UTC):%Y%m%dT%H%MZ}"


@pytest.fixture
def hours(guide: Member) -> Any:
    return TourHoursFactory(guide=guide, weekday=_day().weekday(), seats=4)


def _page(path: str) -> str:
    return Client(HTTP_HOST=HOST).get(path).content.decode()


def _current(content: str) -> str:
    """The step the bar marks current."""
    start = content.index('aria-current="step"')
    return content[start : content.index("</li>", start)]


def describe_the_step_bar():
    def it_starts_on_day_and_time(guide: Member, hours: Any):
        content = _page("/")
        assert "data-tour-steps" in content
        assert 'data-tour-step="time"' in _current(content)
        assert content.count('aria-current="step"') == 1

    def it_moves_to_your_details_once_a_time_is_picked(guide: Member, hours: Any):
        content = _page(f"/?tour={_key(guide)}")
        assert 'data-tour-step="details"' in _current(content)
        # The finished step is a way back, to the same day.
        assert f'href="/?date={_day():%Y-%m-%d}" class="pl-tourpub__step pl-tourpub__step--done"' in content

    def it_ends_on_booked(guide: Member):
        booking = TourBookingFactory(guide=guide)
        content = _page(reverse("tours_public:manage", args=[booking.self_serve_token]) + "?booked=1")
        assert 'data-tour-step="booked"' in _current(content)

    def it_leaves_the_bar_off_a_booking_opened_later(guide: Member):
        booking = TourBookingFactory(guide=guide)
        assert "data-tour-steps" not in _page(reverse("tours_public:manage", args=[booking.self_serve_token]))


def describe_the_back_button():
    def it_goes_back_to_the_picked_day(guide: Member, hours: Any):
        content = _page(f"/?tour={_key(guide)}")
        assert (
            f'href="/?date={_day():%Y-%m-%d}" class="pl-btn pl-btn--secondary pl-tourpub__back" data-tour-back'
            in content
        )
        assert "Back to day &amp; time" in content


def describe_keeping_what_was_typed():
    def it_keeps_the_website_form_in_the_tab(guide: Member, hours: Any):
        content = _page(f"/?tour={_key(guide)}")
        assert "data-tour-draft" in content
        assert "js/tour-booking-draft.js" in content

    def it_forgets_it_once_booked(guide: Member):
        booking = TourBookingFactory(guide=guide)
        content = _page(reverse("tours_public:manage", args=[booking.self_serve_token]) + "?booked=1")
        assert "data-tour-draft-clear" in content
        assert "js/tour-booking-draft.js" in content


def describe_the_kiosk():
    @pytest.fixture
    def kiosk_client() -> Client:
        rules = FacilityTourSettings.load()
        rules.rotate_kiosk_key()
        rules.refresh_from_db()
        client = Client(HTTP_HOST=HOST)
        client.get(f"/kiosk/?key={rules.kiosk_key}")
        return client

    def it_shows_the_steps_but_never_keeps_a_strangers_typing(kiosk_client: Client, guide: Member, hours: Any):
        content = kiosk_client.get(f"/kiosk/book/?tour={_key(guide)}").content.decode()
        assert "data-tour-steps" in content
        assert 'data-tour-step="details"' in _current(content)
        assert "data-tour-draft" not in content
        assert "tour-booking-draft.js" not in content
