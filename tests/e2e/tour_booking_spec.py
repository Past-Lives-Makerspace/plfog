"""End-to-end: a visitor books a facility tour on the tours host, then moves and cancels it from the emails (#948).

``live_server`` answers on ``localhost``, so the spec puts that host in ``TOURS_HOSTS`` (its root is
then the booking page, as on tours.pastlives.space) and points ``TOURS_BASE_URL`` at the live server,
so the Reschedule and Cancel links in the emails open here. Signed out throughout. The visitor picks
a day and 4:00 PM, books for 2, lands on the confirmation, follows the confirmation email's
Reschedule link to 5:00 PM, then the moved email's Cancel link (whose page changes nothing until
Yes). The account toggle starts on, so that visitor gets a GUEST account; a second visitor switches it
off and gets none. Google Analytics only ever sees the booking page with its token redacted. A tour three hours away shows the "too close" page; with bookings closed the host shows the
closed message. At a phone's width nothing scrolls sideways.

``CAPTURE_948_DIR=<dir>`` saves each page at desktop (1100) and phone (390) widths in both themes,
and every email the flow sent (plus the reminder) as ``.html`` beside a picture of it. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.core import mail
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.availability import open_tour_at
from tours.models import FacilityTourSettings, TourBooking
from tours.notify import build_reminder_occurrence

CAPTURE_DIR = os.environ.get("CAPTURE_948_DIR")
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}
GUIDE_EMAIL = "tour-guide-booking@example.com"
VISITOR_EMAIL = "sam.rivera@example.com"


def _capture(page, name: str) -> None:
    """Desktop and phone, dark and light, when CAPTURE_948_DIR is set (the tours host has no sidebar)."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        for theme in ("dark", "light"):
            page.evaluate("(light) => window.__plSetTheme(light)", theme == "light")
            page.wait_for_timeout(500)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"948-{name}-{size_name}-{theme}.png"), full_page=True)
    page.evaluate("() => window.__plSetTheme(true)")
    page.set_viewport_size(DESKTOP)


def _save_email(page, name: str, html: str) -> None:
    """Write one email's HTML and a picture of it, when CAPTURE_948_DIR is set."""
    if not CAPTURE_DIR:
        return
    folder = Path(CAPTURE_DIR) / "emails"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"948-email-{name}.html").write_text(html)
    page.set_viewport_size({"width": 600, "height": 900})
    page.set_content(html)
    page.screenshot(path=str(folder / f"948-email-{name}.png"), full_page=True)
    page.set_viewport_size(DESKTOP)


def _html(message) -> str:
    return next(content for content, mimetype in message.alternatives if mimetype == "text/html")


def _link(message, label: str) -> str:
    """The URL after ``<label>: `` in the plain text body."""
    match = re.search(rf"^{label}: (\S+)$", message.body, re.MULTILINE)
    assert match, f"no {label} link in {message.subject!r}"
    return match.group(1)


def _only(subject_start: str, to: str = VISITOR_EMAIL):
    found = [message for message in mail.outbox if message.to == [to] and message.subject.startswith(subject_start)]
    assert len(found) == 1, [message.subject for message in mail.outbox]
    return found[0]


def _page_locations(page) -> list[str]:
    """Every page_location queued for Google (config calls and page views), read off dataLayer."""
    return page.evaluate(
        """() => (window.dataLayer || [])
              .map(a => Array.from(a))
              .map(a => a.find(x => x && typeof x === 'object' && 'page_location' in x))
              .filter(Boolean)
              .map(o => o.page_location)"""
    )


def _make_guide() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=GUIDE_EMAIL, email=GUIDE_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Gina Guide"
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def _no_sideways_scroll(page) -> None:
    page.set_viewport_size(PHONE)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size(DESKTOP)


def describe_tour_booking():
    def it_books_moves_and_cancels_from_the_emails(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        guide = _make_guide()
        day = timezone.localdate() + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        four, five = local(day, 16), local(day, 17)
        # Analytics on, so the spec can read what the token pages would send Google.
        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-TOURS948"
        config.save()

        # The booking page: the date strip and the day's times.
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}/?date={day:%Y-%m-%d}")
        page.locator('[data-tour-page="book"]').wait_for()
        page.locator(f'[data-tour-day="{day:%Y-%m-%d}"][aria-current="date"]').wait_for()
        _capture(page, "booking-page")
        _no_sideways_scroll(page)

        # Pick 4:00 PM: the form opens for it.
        page.locator(".pl-tourpub__time").filter(has_text="4:00 PM").click()
        page.locator("[data-tour-form]").wait_for()
        page.locator('input[name="name"]').fill("Sam Rivera")
        page.locator('input[name="email"]').fill(VISITOR_EMAIL)
        page.locator('select[name="party_size"]').select_option("2")
        _capture(page, "form")
        _no_sideways_scroll(page)
        with page.expect_navigation():
            page.locator("[data-tour-form] button[type=submit]").click()

        # The confirmation, with the time, the address and both buttons.
        page.locator("[data-tour-confirmed]").wait_for()
        assert "2808 SE 9th Ave" in page.locator("[data-tour-facts]").inner_text()
        page.locator("[data-tour-reschedule]").wait_for()
        page.locator("[data-tour-cancel]").wait_for()
        _capture(page, "confirmation")
        booking = TourBooking.objects.get()
        assert (booking.starts_at, booking.party_size) == (four, 2)
        # The account toggle starts on, so the visitor now has a GUEST account, never a member one.
        assert booking.user is not None and booking.user.email == VISITOR_EMAIL
        assert Member.objects.get(user=booking.user).status == Member.Status.GUEST
        tour = open_tour_at(guide, four)
        assert tour is not None and tour.seats_left == 2
        confirmed = _only("Your Past Lives tour is booked")
        guide_booked = _only("New tour booked", to=GUIDE_EMAIL)
        # Google sees the booking page with its token redacted, never the token itself.
        locations = _page_locations(page)
        assert locations and all("/tour/b/redacted/" in location for location in locations), locations
        assert not any(booking.self_serve_token in location for location in locations)

        # Reschedule from the confirmation email.
        page.goto(_link(confirmed, "Reschedule"))
        page.locator('[data-tour-page="reschedule"]').wait_for()
        assert page.locator(f'[data-tour-time$="{four.astimezone(UTC):%Y%m%dT%H%MZ}"]').count() == 0
        _capture(page, "reschedule-page")
        _no_sideways_scroll(page)
        page.locator(".pl-tourpub__time").filter(has_text="5:00 PM").first.click()
        page.locator("[data-tour-move-form]").wait_for()
        _capture(page, "reschedule-confirm")
        with page.expect_navigation():
            page.locator("[data-tour-move-form] button[type=submit]").click()
        page.locator("[data-tour-confirmed]").wait_for()
        booking.refresh_from_db()
        assert (booking.starts_at, booking.sequence) == (five, 1)
        moved = _only("Your Past Lives tour moved")
        invite = moved.attachments[0][1]
        invite = invite.decode() if isinstance(invite, bytes) else invite
        assert f"UID:tour-{booking.pk}@pastlives" in invite and "SEQUENCE:1" in invite
        assert _only("Tour moved", to=GUIDE_EMAIL)

        # Cancel from the moved email: the page changes nothing until Yes.
        page.goto(_link(moved, "Cancel"))
        page.locator("[data-tour-cancel-form]").wait_for()
        booking.refresh_from_db()
        assert booking.status == TourBooking.Status.BOOKED
        _capture(page, "cancel-confirm")
        _no_sideways_scroll(page)
        with page.expect_navigation():
            page.locator("[data-tour-cancel-form] button[type=submit]").click()
        page.locator("[data-tour-cancelled]").wait_for()
        _capture(page, "cancelled")
        booking.refresh_from_db()
        assert booking.status == TourBooking.Status.CANCELLED
        tour = open_tour_at(guide, five)
        assert tour is not None and tour.seats_left == 4
        cancelled = _only("Your Past Lives tour on")
        assert _only("Tour cancelled", to=GUIDE_EMAIL)

        # The account toggle switched off: a booking with no account made.
        page.goto(f"{live_server.url}/?date={day:%Y-%m-%d}")
        page.locator(".pl-tourpub__time").filter(has_text="4:00 PM").click()
        page.locator("[data-tour-form]").wait_for()
        page.locator('input[name="name"]').fill("Pat Noaccount")
        page.locator('input[name="email"]').fill("pat.noaccount@example.com")
        toggle = page.locator('[data-tour-account] input[name="create_account"]')
        assert toggle.is_checked()
        page.locator("[data-tour-account] .pl-toggle").click()
        assert not toggle.is_checked()
        with page.expect_navigation():
            page.locator("[data-tour-form] button[type=submit]").click()
        page.locator("[data-tour-confirmed]").wait_for()
        second = TourBooking.objects.get(visitor_email="pat.noaccount@example.com")
        assert second.user is None
        assert not User.objects.filter(email="pat.noaccount@example.com").exists()

        # Too close to change online.
        soon = TourBookingFactory(guide=guide, starts_at=timezone.now() + timedelta(hours=3), visitor_name="Pat Late")
        page.goto(f"{live_server.url}/tour/b/{soon.self_serve_token}/cancel/")
        page.locator('[data-tour-reason="too_close"]').wait_for()
        _capture(page, "too-close")

        # Bookings closed.
        tour_settings = FacilityTourSettings.load()
        tour_settings.accepting_bookings = False
        tour_settings.save()
        page.goto(f"{live_server.url}/")
        page.locator("[data-tour-closed]").wait_for()
        _capture(page, "closed")

        # Every email this flow sent, and the reminder, for review.
        _save_email(page, "visitor-confirmed", _html(confirmed))
        _save_email(page, "visitor-moved", _html(moved))
        _save_email(page, "visitor-cancelled", _html(cancelled))
        _save_email(page, "guide-booked", _html(guide_booked))
        _save_email(page, "guide-moved", _html(_only("Tour moved", to=GUIDE_EMAIL)))
        _save_email(page, "guide-cancelled", _html(_only("Tour cancelled", to=GUIDE_EMAIL)))
        reminder = build_reminder_occurrence(soon).messages
        assert reminder is not None
        _save_email(page, "visitor-reminder", next(iter(reminder.values())).html_body or "")
