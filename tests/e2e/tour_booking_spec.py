"""End-to-end: a visitor books a facility tour on the tours host, then moves and cancels it from the emails (#948).

``live_server`` answers on ``localhost``, so the spec puts that host in ``TOURS_HOSTS`` (its root is
then the booking page, as on tours.pastlives.space) and points ``TOURS_BASE_URL`` at the live server,
so the Reschedule and Cancel links in the emails open here. Signed out throughout. The visitor picks
a day and 4:00 PM, books for 2, lands on the confirmation, follows the confirmation email's
Reschedule link to 5:00 PM, then the moved email's Cancel link (whose page changes nothing until
Yes). The account toggle starts on, so that visitor gets a GUEST account; a second visitor switches it
off and gets none. Google Analytics only ever sees the booking page with its token redacted. A tour three hours away shows the "too close" page; with bookings closed the host shows the
closed message. At a phone's width nothing scrolls sideways.

The second spec (#1277) holds a boosted day link's request to see the whole page loading (the gold
top bar and the dim) while a second tap sends nothing, then shows a failed htmx request and a
failed native validation leave the page usable.

``CAPTURE_948_DIR=<dir>`` saves each page at desktop (1100) and phone (390) widths in both themes (the loading state at 1280 and 390),
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
from playwright.sync_api import expect

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
        page.locator('input[name="phone"]').fill("503 555 0142")
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
        # No account is made at booking (Morlock, 2026-11-22); signing in later claims it (claim_tours).
        assert booking.user is None
        assert not User.objects.filter(email=VISITOR_EMAIL).exists()
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
        invite = moved.attachments[0].get_content()
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

        # No account switch and no account made (Morlock, 2026-11-22): a tour finds its visitor's
        # account by verified email when they sign in.
        page.goto(f"{live_server.url}/?date={day:%Y-%m-%d}")
        page.locator(".pl-tourpub__time").filter(has_text="4:00 PM").click()
        page.locator("[data-tour-form]").wait_for()
        assert page.locator('input[name="create_account"]').count() == 0
        page.locator('input[name="name"]').fill("Pat Noaccount")
        page.locator('input[name="email"]').fill("pat.noaccount@example.com")
        page.locator('input[name="phone"]').fill("503 555 0142")
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


# The tours site's loading state (#1277): the bar and the scrim on <html>, the body busy, nothing inside a control.
LOADING_STATE = """() => ({
    loading: document.documentElement.getAttribute("data-tours-loading"),
    scrim: (() => {
        const el = document.querySelector("[data-tours-loading-indicator]");
        return el ? getComputedStyle(el).display : "none";
    })(),
    bodyBusy: document.body.getAttribute("aria-busy"),
    inControl: document.querySelectorAll('[aria-busy="true"]:not(body)').length,
})"""
LOADING = {"loading": "on", "scrim": "block", "bodyBusy": "true", "inControl": 0}
IDLE = {"loading": None, "scrim": "none", "bodyBusy": None, "inControl": 0}
SETTLED = "() => !document.documentElement.hasAttribute('data-tours-loading')"
WIDE = {"width": 1280, "height": 900}


def _capture_loading(page, name: str) -> None:
    """The loading state at desktop (1280) and phone (390), dark and light, its bar frozen at 60% for a still."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    page.add_style_tag(
        content=".pl-tours-loading__bar { animation: none !important; transform: scaleX(0.6) !important; }"
    )
    for size_name, size in (("desktop", WIDE), ("phone", PHONE)):
        page.set_viewport_size(size)
        for theme in ("dark", "light"):
            page.evaluate("(light) => window.__plSetTheme(light)", theme == "light")
            page.wait_for_timeout(500)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1277-{name}-{size_name}-{theme}.png"))
    page.evaluate("() => window.__plSetTheme(true)")
    page.set_viewport_size(DESKTOP)


def describe_tours_loading_indicator():
    def it_dims_the_page_under_a_top_bar_until_the_response_lands(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        guide = _make_guide()
        first, second = (timezone.localdate() + timedelta(days=n) for n in (4, 5))
        for day in (first, second):
            TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}/?date={first:%Y-%m-%d}")
        page.locator(f'[data-tour-day="{first:%Y-%m-%d}"][aria-current="date"]').wait_for()
        assert page.evaluate(LOADING_STATE) == IDLE

        # A boosted day link: the bar and the dim in the same tap, while its request is held.
        sent: list[str] = []
        page.on("request", lambda request: sent.append(request.url) if "?date=" in request.url else None)
        held = []
        page.route(lambda url: url.endswith(f"?date={second:%Y-%m-%d}"), lambda route: held.append(route))
        page.locator(f'[data-tour-day="{second:%Y-%m-%d}"]').click()
        page.wait_for_function("() => document.documentElement.getAttribute('data-tours-loading') === 'on'")
        assert page.evaluate(LOADING_STATE) == LOADING

        # A second tap, by pointer (it lands on the scrim) or on the link itself, sends nothing.
        box = page.locator(f'[data-tour-day="{first:%Y-%m-%d}"]').bounding_box()
        assert box is not None
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.evaluate(f"() => document.querySelector('[data-tour-day=\"{first:%Y-%m-%d}\"]').click()")
        page.wait_for_timeout(300)
        assert len(sent) == 1 and sent[0].endswith(f"?date={second:%Y-%m-%d}")
        _capture_loading(page, "booking-page")

        # The response lands: the new day, and the page takes taps again.
        held[0].continue_()
        page.locator(f'[data-tour-day="{second:%Y-%m-%d}"][aria-current="date"]').wait_for()
        page.wait_for_function(SETTLED)
        assert page.evaluate(LOADING_STATE) == IDLE

        # A failed htmx request leaves nothing dimmed or blocked.
        page.route(
            lambda url: url.endswith(f"?date={first:%Y-%m-%d}"),
            lambda route: route.fulfill(status=500, body="Server Error"),
        )
        page.locator(f'[data-tour-day="{first:%Y-%m-%d}"]').click()
        page.wait_for_function(SETTLED)
        assert page.evaluate(LOADING_STATE) == IDLE
        page.locator(f'[data-tour-day="{second:%Y-%m-%d}"][aria-current="date"]').wait_for()

        # Still usable: a time opens the form, and a submit that fails native validation shows nothing.
        page.locator(".pl-tourpub__time").filter(has_text="4:00 PM").click()
        page.locator("[data-tour-form]").wait_for()
        page.wait_for_function(SETTLED)
        page.locator("[data-tour-form] button[type=submit]").click()
        assert page.evaluate(LOADING_STATE) == IDLE
        assert page.locator("[data-tour-form] button[type=submit]").text_content() == "Book My Tour"


def describe_the_month_calendar_on_a_phone():
    """The month calendar (Morlock, 2026-11-22: "Is it going to work well on mobile?"): it fits its card
    on the narrowest phone, and its days are big enough to tap on a common one."""

    _FITS = """() => {
        const card = document.querySelector('.pl-tourpub').getBoundingClientRect();
        const months = Array.from(document.querySelectorAll('.pl-tourpub__month'));
        return months.every((month) => {
            const box = month.getBoundingClientRect();
            return box.left >= card.left && box.right <= card.right;
        });
    }"""
    _DAY = "() => document.querySelector('[data-tour-day]').getBoundingClientRect().width"

    def it_fits_and_taps_well(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        guide = _make_guide()
        day = timezone.localdate() + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        for width in (320, 375, 390):
            page.set_viewport_size({"width": width, "height": 800})
            page.goto(f"{live_server.url}/?date={day:%Y-%m-%d}")
            page.locator(f'[data-tour-day="{day:%Y-%m-%d}"]').wait_for()
            assert page.evaluate(_FITS), f"the calendar spills out of its card at {width}px"
            assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), width
            if width >= 375:
                # Apple's guide is 44pt; 40 is the floor a thumb hits reliably on a seven column grid.
                assert page.evaluate(_DAY) >= 40, f"days are {page.evaluate(_DAY)}px wide at {width}px"


def describe_the_booking_steps():
    """Morlock, 2026-11-22: the step bar (Schedule, Details, Confirm) as the way back and forth, and what was typed kept."""

    def it_keeps_the_typing_across_back_and_forgets_it_once_booked(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        guide = _make_guide()
        day = timezone.localdate() + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        four = local(day, 16)
        key = f"{guide.pk}-{four.astimezone(UTC):%Y%m%dT%H%MZ}"
        page.set_viewport_size(DESKTOP)

        page.goto(f"{live_server.url}/?tour={key}")
        page.locator('[data-tour-step="details"][aria-current="step"]').wait_for()
        page.locator('input[name="name"]').fill("Sam Rivera")
        page.locator('input[name="email"]').fill("sam@example.com")
        page.locator('input[name="phone"]').fill("503 555 0142")

        # The step bar is the way back and forth: Schedule back, then Details forward to the same time.
        page.locator('a[data-tour-step="schedule"]').click()
        page.locator('[data-tour-step="schedule"][aria-current="step"]').wait_for()
        page.locator('a[data-tour-step="details"]').click()
        page.locator('[data-tour-step="details"][aria-current="step"]').wait_for()
        expect(page.locator('input[name="name"]')).to_have_value("Sam Rivera")
        expect(page.locator('input[name="email"]')).to_have_value("sam@example.com")
        # And through the calendar: picking the time again finds the typing too.
        page.locator('a[data-tour-step="schedule"]').click()
        page.locator(f'[data-tour-time="{key}"]').click()
        page.locator('[data-tour-step="details"][aria-current="step"]').wait_for()
        expect(page.locator('input[name="name"]')).to_have_value("Sam Rivera")

        with page.expect_navigation():
            page.locator("[data-tour-form] button[type=submit]").click()
        page.locator('[data-tour-step="confirm"][aria-current="step"]').wait_for()
        assert page.evaluate("() => sessionStorage.getItem('pl-tour-booking-draft')") is None
        assert TourBooking.objects.get().visitor_name == "Sam Rivera"
