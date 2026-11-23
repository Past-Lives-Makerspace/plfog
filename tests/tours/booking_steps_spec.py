"""The booking page's step bar (Morlock, 2026-11-22): Schedule, Details, Confirm, the current one
highlighted and the bar itself the way back and forth (no separate Back), and what the visitor typed
kept in their browser tab (never at the lobby kiosk, a shared screen)."""

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
    def it_starts_on_schedule(guide: Member, hours: Any):
        content = _page("/")
        assert "data-tour-steps" in content
        assert 'data-tour-step="schedule"' in _current(content)
        assert content.count('aria-current="step"') == 1

    def it_moves_to_details_once_a_time_is_picked(guide: Member, hours: Any):
        content = _page(f"/?tour={_key(guide)}")
        assert 'data-tour-step="details"' in _current(content)
        # The finished step is a way back, to the same day.
        assert f'href="/?date={_day():%Y-%m-%d}" class="pl-tourpub__step pl-tourpub__step--done"' in content

    def it_ends_on_confirm(guide: Member):
        booking = TourBookingFactory(guide=guide)
        content = _page(reverse("tours_public:manage", args=[booking.self_serve_token]) + "?booked=1")
        assert 'data-tour-step="confirm"' in _current(content)

    def it_leaves_the_bar_off_a_booking_opened_later(guide: Member):
        booking = TourBookingFactory(guide=guide)
        assert "data-tour-steps" not in _page(reverse("tours_public:manage", args=[booking.self_serve_token]))


def describe_moving_between_steps():
    def it_has_no_separate_back_button(guide: Member, hours: Any):
        assert "data-tour-back" not in _page(f"/?tour={_key(guide)}")

    def it_names_the_steps_schedule_details_confirm(guide: Member, hours: Any):
        content = _page("/")
        bar = content[content.index("data-tour-steps") : content.index("</ol>", content.index("data-tour-steps"))]
        assert [name for name in ("Schedule", "Details", "Confirm") if name in bar] == [
            "Schedule",
            "Details",
            "Confirm",
        ]

    def it_leaves_details_for_the_browser_to_link_from_schedule(guide: Member, hours: Any):
        # The server cannot know the time picked before; tour-booking-draft.js links it.
        assert "data-tour-step-later" in _page("/")


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
        assert f'href="/kiosk/book/?date={_day():%Y-%m-%d}" class="pl-tourpub__step pl-tourpub__step--done"' in content
        assert 'data-tour-step="details"' in _current(content)
        assert "data-tour-draft" not in content
        assert "tour-booking-draft.js" not in content
