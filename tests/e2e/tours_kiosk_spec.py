"""End-to-end: cairn's front desk kiosk on the tours host (#950), check in or schedule a tour.

``live_server`` answers on ``localhost``, so the kiosk spec puts that host in ``TOURS_HOSTS`` (as on
tours.pastlives.space). Without the kiosk key ``/kiosk/`` is the public booking page with no names;
the kiosk link (``/kiosk/?key=``) sets the cookie and lands on the kiosk home. A visitor checks in
by email, by tapping their name in today's list, and by typing a name that matches two tours (the
pick list). A tapped name or a pick list choice asks "Is this you?" first (Not Me checks in nobody); a
name that matches nothing shows the try again screen. Each check in stamps the
booking and rings the guide's bell. A tour is scheduled from the kiosk (source kiosk). The kiosk
returns home 60 seconds after the last input and a few seconds after a finished screen, and the
back button brings no visitor back. Rotating the key locks the screen out. Driven on Playwright's
fake clock, so no spec waits real seconds.

The Settings tab's kiosk link is the second spec (the members host, signed in as an admin).

The third spec drives the kiosk on the keyboard alone (#1155, cairn's mouse went missing): with no
mouse plugged in the Keyboard Only Mode badge shows, and arrow keys, Tab, Enter and Escape check a
visitor in and book a tour. ``matchMedia("(any-pointer: fine)")`` is stubbed, since headless
Chromium reports a mouse; ``window.__plMouse(true)`` plugs one in.

The fourth (#1273) walks every Back, checks the guides' first names beside the times, in the form
(following the Guide dropdown) and on Is This You and the pick list, and holds a request in flight to
see a pressed link or button go busy while every other control is disabled and a second press sends
nothing; a page restored from the back/forward cache gets its controls back.

``CAPTURE_950_DIR=<dir>`` saves every kiosk screen (home, check in, list, confirm, welcome, pick list, no match, schedule picker,
form, confirmation) at cairn's 1920 by 1080 in both themes, and the
Settings tab's kiosk section at desktop (1100) and phone (390, sidebar drawer closed) widths in both
themes. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import time, timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.models import Notification
from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import FacilityTourSettings, TourBooking

CAPTURE_DIR = os.environ.get("CAPTURE_950_DIR")
CAIRN = {"width": 1920, "height": 1080}
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}
GUIDE_EMAIL = "tour-guide-kiosk@example.com"
ADMIN_EMAIL = "tour-admin-kiosk@example.com"
HOME = reverse("tours_kiosk:home")


def _capture(page, name: str, *, element: str = "") -> None:
    """The kiosk screen (or one ``element`` of it) at 1920 by 1080, dark and light, when CAPTURE_950_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        page.evaluate("(light) => window.__plSetTheme(light)", theme == "light")
        page.wait_for_timeout(400)
        path = str(Path(CAPTURE_DIR) / f"950-kiosk-{name}-{theme}.png")
        if element:
            page.locator(element).screenshot(path=path)
        else:
            page.screenshot(path=path, full_page=True)
    page.evaluate("() => window.__plSetTheme(true)")


def _capture_hub(page, name: str) -> None:
    """A hub page at desktop and phone widths (the drawer closed on a phone), dark and light."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        _set_drawer(page, is_open=size is DESKTOP)
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(600)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"950-{name}-{size_name}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _set_drawer(page, *, is_open: bool) -> None:
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


def _member(email: str, name: str, **fields: object) -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    for field, value in fields.items():
        setattr(member, field, value)
    member.save()
    return member


def _screen(page, name: str) -> None:
    expect(page.locator(f'[data-kiosk-screen="{name}"]')).to_be_visible()


def _hold(page) -> None:
    """Stop the page's clock where it is, so a finished screen stays up while it is checked and pictured.

    Python's ``pause_at`` reads a number as seconds since the epoch (``parse_time`` multiplies by
    1000), while ``Date.now()`` is milliseconds: passed raw, the clock jumped millennia ahead and
    fired the 8 second timer at once.
    """
    page.clock.pause_at(page.evaluate("() => Date.now()") / 1000 + 0.05)


def _goes_home_on_its_own(page, live_server, screen: str) -> None:
    """A finished screen stays up until its 8 second timer, then goes home by itself.

    The page loaded a moment before :func:`_hold`, so 6.5 seconds of page time is still inside the
    timer and 2 more are past it.
    """
    page.clock.run_for(6_500)
    _screen(page, screen)
    assert page.url != f"{live_server.url}{HOME}"
    page.clock.run_for(2_000)
    _at_home(page, live_server)
    page.clock.resume()


def _at_home(page, live_server) -> None:
    page.wait_for_url(f"{live_server.url}{HOME}")
    _screen(page, "home")


# Headless Chromium always reports a mouse: this answers "(any-pointer: fine)" from window.__plMouse.
NO_MOUSE = """(() => {
    const listeners = [];
    const fine = { matches: false, media: "(any-pointer: fine)", addEventListener: (_, f) => listeners.push(f), removeEventListener() {} };
    const real = window.matchMedia.bind(window);
    window.matchMedia = (query) => (query === "(any-pointer: fine)" ? fine : real(query));
    window.__plMouse = (on) => { fine.matches = on; listeners.forEach((f) => f({ matches: on })); };
})();"""


def _focused(page, selector: str) -> None:
    expect(page.locator(selector)).to_be_focused()


def _check_in_screen(page) -> None:
    page.locator('[data-kiosk-go="check-in"]').click()
    _screen(page, "check-in")


def _welcomed(page, live_server, first_name: str, guide_line: str) -> None:
    """The welcome, with the guide's name, then home a few seconds later on its own."""
    _screen(page, "welcome")
    _hold(page)
    expect(page.locator("[data-kiosk-welcome]")).to_have_text(f"Welcome, {first_name}!")
    expect(page.locator("[data-kiosk-guide]")).to_have_text(guide_line)


def describe_tours_kiosk():
    def it_checks_in_schedules_and_resets(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        guide = _member(GUIDE_EMAIL, "Gina Guide")
        guide.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
        today = timezone.localdate()
        pat = TourBookingFactory(
            guide=guide, starts_at=local(today, 15), visitor_name="Pat Lee", visitor_email="pat.lee@example.com"
        )
        jo = TourBookingFactory(guide=guide, starts_at=local(today, 15, 30), visitor_name="Jo Tapper")
        sam = TourBookingFactory(guide=guide, starts_at=local(today, 16), visitor_name="Sam Rivera")
        ortiz = TourBookingFactory(guide=guide, starts_at=local(today, 17), visitor_name="Sam Ortiz", party_size=3)
        TourBookingFactory(
            guide=guide, starts_at=local(today, 18), visitor_name="Cy Cancelled", status=TourBooking.Status.CANCELLED
        )
        TourBookingFactory(guide=guide, starts_at=local(today + timedelta(days=1), 16), visitor_name="Tom Morrow")
        day = today + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        key = FacilityTourSettings.load().rotate_kiosk_key()
        page.set_viewport_size(CAIRN)
        page.clock.install()

        # No key: the public booking page, and no names.
        page.goto(f"{live_server.url}{HOME}")
        expect(page.locator('[data-tour-page="book"]')).to_be_visible()
        assert "Sam R." not in page.content() and "Pat L." not in page.content()

        # The kiosk link: the cookie, then the kiosk home without the key in the address.
        page.goto(f"{live_server.url}{HOME}?key={key}")
        _at_home(page, live_server)
        expect(page.locator(".cp-topbar__nav")).to_have_count(0)
        _capture(page, "home")

        # Check in: all three ways at once, today's booked tours only.
        _check_in_screen(page)
        names = page.locator("[data-kiosk-list]")
        for listed in ("Pat L.", "Jo T.", "Sam R.", "Sam O."):
            expect(names).to_contain_text(listed)
        expect(names).not_to_contain_text("Cy C.")
        expect(names).not_to_contain_text("Tom M.")
        expect(page.locator("#kiosk-email")).to_be_focused()
        _capture(page, "check-in")
        _capture(page, "list", element="[data-kiosk-list]")

        # By email, typed in any case: welcome, the guide's bell, then home on its own.
        page.locator("#kiosk-email").fill("PAT.Lee@Example.com")
        page.locator("#kiosk-email").press("Enter")
        _welcomed(page, live_server, "Pat", "Gina will be right with you.")
        _capture(page, "welcome")
        pat.refresh_from_db()
        assert pat.checked_in_at is not None and pat.status == TourBooking.Status.BOOKED
        bell = Notification.objects.get(user=guide.user, trigger="tour.checked_in")
        assert bell.title == "Your 3:00 tour is here"
        _goes_home_on_its_own(page, live_server, "welcome")
        # The back button brings back no visitor: the welcome was shown once, the form comes back empty.
        page.go_back()
        _screen(page, "check-in")
        expect(page.locator("#kiosk-email")).to_have_value("")
        expect(page.locator("[data-kiosk-welcome]")).to_have_count(0)
        page.locator("[data-kiosk-back]").click()
        _at_home(page, live_server)

        # By tapping a name: "Is this you?" first. Not Me goes back and checks in nobody.
        _check_in_screen(page)
        page.locator(f'[data-kiosk-name="{sam.pk}"]').click()
        _screen(page, "confirm")
        expect(page.locator("[data-kiosk-confirm-who]")).to_have_text("Sam R., 4:00 PM")
        page.locator("[data-kiosk-not-me]").click()
        _screen(page, "check-in")
        sam.refresh_from_db()
        assert sam.checked_in_at is None
        page.locator(f'[data-kiosk-name="{jo.pk}"]').click()
        _screen(page, "confirm")
        expect(page.locator("[data-kiosk-confirm-who]")).to_have_text("Jo T., 3:30 PM")
        expect(page.locator("[data-kiosk-confirm-yes]")).to_be_focused()
        _capture(page, "confirm")
        jo.refresh_from_db()
        assert jo.checked_in_at is None
        page.locator("[data-kiosk-confirm-yes]").click()
        _welcomed(page, live_server, "Jo", "Gina will be right with you.")
        _goes_home_on_its_own(page, live_server, "welcome")
        jo.refresh_from_db()
        assert jo.checked_in_at is not None

        # By typing a name two tours share: the pick list, then the right one.
        _check_in_screen(page)
        page.locator("#kiosk-name").fill("  sam ")
        page.locator("#kiosk-name").press("Enter")
        _screen(page, "pick")
        expect(page.locator("[data-kiosk-pick] [data-kiosk-name]")).to_have_count(2)
        _capture(page, "pick-list")
        page.locator(f'[data-kiosk-pick] [data-kiosk-name="{ortiz.pk}"]').click()
        _screen(page, "confirm")
        expect(page.locator("[data-kiosk-confirm-who]")).to_have_text("Sam O., 5:00 PM")
        page.locator("[data-kiosk-confirm-yes]").click()
        _welcomed(page, live_server, "Sam", "Gina will be right with you.")
        _goes_home_on_its_own(page, live_server, "welcome")
        ortiz.refresh_from_db()
        sam.refresh_from_db()
        assert ortiz.checked_in_at is not None and sam.checked_in_at is None
        assert Notification.objects.filter(user=guide.user, trigger="tour.checked_in").count() == 3

        # A name nobody booked under today: try again, or schedule one.
        _check_in_screen(page)
        page.locator("#kiosk-name").fill("Tom Morrow")
        page.locator("#kiosk-name").press("Enter")
        _screen(page, "no-match")
        expect(page.locator('[data-kiosk-screen="no-match"] h1')).to_have_text("We Could Not Find a Tour Today")
        _capture(page, "no-match")
        page.locator("[data-kiosk-try-again]").click()
        _screen(page, "check-in")

        # Idle: half typed, then 60 seconds without input, home; the field comes back empty.
        page.locator("#kiosk-email").fill("half.typed@")
        page.clock.run_for(61_000)
        _at_home(page, live_server)
        _check_in_screen(page)
        expect(page.locator("#kiosk-email")).to_have_value("")

        # Schedule a tour: the website's date strip and times, the form, the confirmation.
        page.locator("[data-kiosk-back]").click()
        _at_home(page, live_server)
        page.locator('[data-kiosk-go="book"]').click()
        _screen(page, "book")
        page.locator(f'[data-tour-day="{day:%Y-%m-%d}"]').click()
        expect(page.locator(f'[data-tour-day="{day:%Y-%m-%d}"][aria-current="date"]')).to_be_visible()
        _capture(page, "schedule-picker")
        page.locator(".pl-tourpub__time").filter(has_text="4:00 PM").click()
        expect(page.locator("[data-tour-form]")).to_be_visible()
        expect(page.locator('[data-tour-account] input[name="create_account"]')).to_be_checked()
        page.locator('input[name="name"]').fill("Kim Kiosk")
        page.locator('input[name="email"]').fill("kim.kiosk@example.com")
        page.locator('select[name="party_size"]').select_option("2")
        _capture(page, "form")
        page.locator("[data-tour-form] button[type=submit]").click()
        _screen(page, "booked")
        _hold(page)
        expect(page.locator("[data-kiosk-booked]")).to_have_text("You're Booked, Kim!")
        expect(page.locator("[data-kiosk-booked-guide]")).to_have_text("Your guide is Gina.")
        _capture(page, "confirmation")
        kim = TourBooking.objects.get(visitor_email="kim.kiosk@example.com")
        assert kim.source == TourBooking.Source.KIOSK and kim.party_size == 2
        assert kim.starts_at == local(day, 16)
        assert kim.self_serve_token not in page.content()
        _goes_home_on_its_own(page, live_server, "booked")

        # Rotating the key locks this screen out: the public booking page again.
        FacilityTourSettings.load().rotate_kiosk_key()
        page.goto(f"{live_server.url}{HOME}")
        expect(page.locator('[data-tour-page="book"]')).to_be_visible()
        assert "Sam R." not in page.content()

    def it_shows_the_kiosk_link_on_the_settings_tab(live_server, page, login_via_code, settings):
        _member(ADMIN_EMAIL, "Ada Admin", fog_role=Member.FogRole.ADMIN)
        login_via_code(ADMIN_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:settings')}")
        section = page.locator("[data-tour-settings-kiosk]")
        expect(section.locator("[data-tour-kiosk-create]")).to_be_visible()
        with page.expect_navigation():
            section.locator("[data-tour-kiosk-create]").click()
        key = FacilityTourSettings.load().kiosk_key
        assert key
        expect(page.locator("[data-tour-kiosk-url]")).to_have_value(f"{settings.TOURS_BASE_URL}{HOME}?key={key}")
        _capture_hub(page, "settings-kiosk")

        # Rotate asks first, then gives a new link.
        page.locator("[data-tour-kiosk-rotate]").click()
        dialog = page.locator('[role="dialog"]').filter(has_text="Rotate the Kiosk Key?")
        expect(dialog).to_be_visible()
        with page.expect_navigation():
            dialog.get_by_role("button", name="Rotate Key").click()
        rotated = FacilityTourSettings.load().kiosk_key
        assert rotated and rotated != key
        expect(page.locator("[data-tour-kiosk-url]")).to_have_value(f"{settings.TOURS_BASE_URL}{HOME}?key={rotated}")

    def it_works_from_the_keyboard_alone(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        guide = _member(GUIDE_EMAIL, "Gina Guide")
        guide.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
        today = timezone.localdate()
        pat = TourBookingFactory(
            guide=guide, starts_at=local(today, 15), visitor_name="Pat Lee", visitor_email="pat.lee@example.com"
        )
        sam = TourBookingFactory(guide=guide, starts_at=local(today, 16), visitor_name="Sam Rivera")
        day = today + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        key = FacilityTourSettings.load().rotate_kiosk_key()
        page.set_viewport_size(CAIRN)
        page.clock.install()
        page.add_init_script(NO_MOUSE)
        page.goto(f"{live_server.url}{HOME}?key={key}")
        _at_home(page, live_server)

        # No mouse: the badge names the keys. Plugging one in hides it, pulling it out shows it again.
        badge = page.locator("[data-kiosk-keys]")
        expect(badge).to_be_visible()
        expect(badge).to_contain_text("Keyboard Only Mode")
        expect(badge).to_contain_text("No mouse detected")
        _capture(page, "keyboard-home")
        page.evaluate("() => window.__plMouse(true)")
        expect(badge).to_be_hidden()
        page.evaluate("() => window.__plMouse(false)")
        expect(badge).to_be_visible()

        # Home: the arrow keys move between the two cards, Enter opens one.
        _focused(page, '[data-kiosk-go="check-in"]')
        page.keyboard.press("ArrowRight")
        _focused(page, '[data-kiosk-go="book"]')
        page.keyboard.press("ArrowLeft")
        _focused(page, '[data-kiosk-go="check-in"]')
        page.keyboard.press("Enter")
        _screen(page, "check-in")

        # Back and Start Over (#1273): Shift+Tab reaches the heading, the arrow keys move between them.
        _focused(page, "#kiosk-email")
        page.keyboard.press("Shift+Tab")
        _focused(page, "[data-kiosk-back]")
        page.keyboard.press("ArrowLeft")
        _focused(page, "[data-kiosk-step-back]")
        page.keyboard.press("ArrowRight")
        _focused(page, "[data-kiosk-back]")
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("Enter")
        _at_home(page, live_server)
        page.keyboard.press("Enter")
        _screen(page, "check-in")

        # Check in by email: typed into the focused field, Enter, then Done on the welcome.
        _focused(page, "#kiosk-email")
        page.keyboard.type("pat.lee@example.com")
        page.keyboard.press("Enter")
        _screen(page, "welcome")
        _focused(page, "[data-kiosk-done-button]")
        page.keyboard.press("Enter")
        _at_home(page, live_server)
        pat.refresh_from_db()
        assert pat.checked_in_at is not None

        # Check in by name from the list: Tab past the two forms, arrow down the names, confirm.
        page.keyboard.press("Enter")
        _screen(page, "check-in")
        for _ in range(4):
            page.keyboard.press("Tab")
        _focused(page, f'[data-kiosk-name="{pat.pk}"]')
        page.keyboard.press("ArrowRight")
        _focused(page, f'[data-kiosk-name="{sam.pk}"]')
        page.keyboard.press("Enter")
        _screen(page, "confirm")
        _focused(page, "[data-kiosk-confirm-yes]")
        page.keyboard.press("ArrowRight")
        _focused(page, "[data-kiosk-not-me]")
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("Enter")
        _screen(page, "welcome")
        sam.refresh_from_db()
        assert sam.checked_in_at is not None
        page.keyboard.press("Enter")
        _at_home(page, live_server)

        # Escape starts over from anywhere, half typed or not.
        page.keyboard.press("Enter")
        _screen(page, "check-in")
        page.keyboard.type("half.typed@")
        page.keyboard.press("Escape")
        _at_home(page, live_server)

        # Schedule a tour: the picked day's first time has the focus, the day strip is one tab stop.
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Enter")
        _screen(page, "book")
        first_time = page.locator(".pl-tourpub__time").filter(has_text="4:00 PM")
        expect(first_time).to_be_focused()
        _capture(page, "keyboard-picker")
        page.keyboard.press("ArrowRight")
        expect(page.locator(".pl-tourpub__time").filter(has_text="5:00 PM")).to_be_focused()
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("Shift+Tab")
        _focused(page, f'[data-tour-day="{day:%Y-%m-%d}"]')
        page.keyboard.press("ArrowRight")
        _focused(page, f'[data-tour-day="{day + timedelta(days=7):%Y-%m-%d}"]')
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("Tab")
        expect(first_time).to_be_focused()
        page.keyboard.press("Enter")

        # The form: its first field has the focus; Tab through, a digit picks the party size.
        expect(page.locator("[data-tour-form]")).to_be_visible()
        _focused(page, 'input[name="name"]')
        page.keyboard.type("Kim Keys")
        page.keyboard.press("Tab")
        page.keyboard.type("kim.keys@example.com")
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        _focused(page, 'select[name="party_size"]')
        page.keyboard.type("2")
        page.keyboard.press("Tab")
        _focused(page, 'input[name="create_account"]')
        _capture(page, "keyboard-form")
        # Enter flips the switch, never books: the visitor turned the account off.
        page.keyboard.press("Enter")
        expect(page.locator('input[name="create_account"]')).not_to_be_checked()
        assert not TourBooking.objects.filter(visitor_email="kim.keys@example.com").exists()
        page.keyboard.press("Tab")
        _focused(page, "[data-tour-form] button[type=submit]")
        page.keyboard.press("Enter")
        _screen(page, "booked")
        _focused(page, "[data-kiosk-done-button]")
        kim = TourBooking.objects.get(visitor_email="kim.keys@example.com")
        assert kim.source == TourBooking.Source.KIOSK and kim.party_size == 2
        assert kim.starts_at == local(day, 16)
        page.keyboard.press("Enter")
        _at_home(page, live_server)


def _back_to(page, screen: str) -> None:
    page.locator("[data-kiosk-step-back]").click()
    _screen(page, screen)


BUSY_STATE = """() => ({
    busy: [...document.querySelectorAll('[aria-busy="true"]')].length,
    labels: document.querySelectorAll("[data-kiosk-busy-label]").length,
    off: [...document.querySelectorAll("a[href], button, input, select")].filter((el) => el.disabled || el.getAttribute("aria-disabled") === "true").length,
    marked: document.body.hasAttribute("data-kiosk-busy"),
})"""


IDLE = {"busy": None, "label": None, "disabled": False, "ariaDisabled": None, "tabindex": None}

# Once, on the next press (event "click" or "submit", after tours-kiosk.js marked it busy): each
# selector's busy state, kept in sessionStorage past the page change; then the pressed control is
# pressed again, a link elsewhere is clicked and the form resubmitted, all of which must send nothing.
RECORD_PRESS = """([kind, selectors]) => {
    const read = (el) => {
        const label = el.querySelector("[data-kiosk-busy-label]");
        return {
            busy: el.getAttribute("aria-busy"),
            label: label ? label.textContent : null,
            disabled: el.disabled === true,
            ariaDisabled: el.getAttribute("aria-disabled"),
            tabindex: el.getAttribute("tabindex"),
        };
    };
    const once = (event) => {
        document.removeEventListener(kind, once);
        const pressed = event.submitter || event.target.closest("a[href]");
        sessionStorage.setItem("kioskPress", JSON.stringify(selectors.map((sel) => read(document.querySelector(sel)))));
        pressed.click();
        document.querySelector("a[href]:not([aria-busy])").click();
        if (pressed.form) pressed.form.requestSubmit(pressed);
    };
    document.addEventListener(kind, once);
}"""


def _record_press(page, kind: str, selectors: list[str]) -> None:
    page.evaluate(RECORD_PRESS, [kind, selectors])


def _recorded(page) -> list[dict]:
    return page.evaluate("() => JSON.parse(sessionStorage.getItem('kioskPress'))")


def describe_kiosk_back_names_and_busy():
    def it_steps_back_names_guides_and_shows_every_tap(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        gina = _member(GUIDE_EMAIL, "Gina Guide")
        olive = _member("olive-kiosk@example.com", "Olive Other")
        for guide in (gina, olive):
            guide.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
        today = timezone.localdate()
        sam = TourBookingFactory(guide=gina, starts_at=local(today, 16), visitor_name="Sam Rivera")
        TourBookingFactory(guide=olive, starts_at=local(today, 17), visitor_name="Sam Ortiz")
        day = today + timedelta(days=4)
        TourHoursFactory(guide=gina, weekday=day.weekday(), seats=4)
        TourHoursFactory(guide=olive, weekday=day.weekday(), seats=4, start_time=time(17), end_time=time(18))
        key = FacilityTourSettings.load().rotate_kiosk_key()
        page.set_viewport_size(CAIRN)
        page.goto(f"{live_server.url}{HOME}?key={key}")
        _at_home(page, live_server)
        expect(page.locator("[data-kiosk-step-back]")).to_have_count(0)

        # Check in: Back goes home.
        _check_in_screen(page)
        _back_to(page, "home")

        # The times name their guide; the form names it too, with Back in place of Pick a Different Time.
        page.locator('[data-kiosk-go="book"]').click()
        _screen(page, "book")
        page.locator(f'[data-tour-day="{day:%Y-%m-%d}"]').click()
        picked_day = page.locator(f'[data-tour-day="{day:%Y-%m-%d}"][aria-current="date"]')
        expect(picked_day).to_be_visible()
        expect(page.locator(".pl-tourpub__time").filter(has_text="4:00 PM")).to_contain_text("with Gina")
        expect(page.locator(".pl-tourpub__time").filter(has_text="5:00 PM")).to_contain_text("with Gina or Olive")
        _capture(page, "1273-times")
        page.locator(".pl-tourpub__time").filter(has_text="4:00 PM").click()
        expect(page.locator("[data-tour-form]")).to_be_visible()
        expect(page.locator("[data-tour-guide-name]")).to_have_text("with Gina")
        expect(page.locator("[data-tour-change]")).to_have_count(0)
        _capture(page, "1273-form")
        _back_to(page, "book")
        expect(picked_day).to_be_visible()
        expect(page.locator("[data-tour-form]")).to_have_count(0)

        # A time two guides share: No preference until the dropdown picks one.
        page.locator(".pl-tourpub__time").filter(has_text="5:00 PM").click()
        names = page.locator("[data-tour-guide-name]")
        expect(names).to_have_text("No preference")
        page.locator("[data-tour-guide]").select_option(label="Olive (4 seats left)")
        expect(names).to_have_text("with Olive")
        page.locator("[data-tour-guide]").select_option(label="No preference")
        expect(names).to_have_text("No preference")
        _back_to(page, "book")
        _back_to(page, "home")

        # Is This You names the guide; Back goes to check in.
        _check_in_screen(page)
        page.locator(f'[data-kiosk-name="{sam.pk}"]').click()
        _screen(page, "confirm")
        expect(page.locator("[data-kiosk-confirm-guide]")).to_have_text("Your guide is Gina.")
        _capture(page, "1273-confirm")
        _back_to(page, "check-in")

        # The pick list names each tour's guide; Back goes to check in.
        page.locator("#kiosk-name").fill("sam")
        page.locator("#kiosk-name").press("Enter")
        _screen(page, "pick")
        expect(page.locator("[data-kiosk-pick]")).to_contain_text("4:00 PM with Gina")
        expect(page.locator("[data-kiosk-pick]")).to_contain_text("5:00 PM with Olive")
        _back_to(page, "check-in")

        # No match: Back goes to check in.
        page.locator("#kiosk-name").fill("Nobody Here")
        page.locator("#kiosk-name").press("Enter")
        _screen(page, "no-match")
        _back_to(page, "check-in")
        page.locator("[data-kiosk-back]").click()
        _at_home(page, live_server)

        # A pressed link: spinner and label at once, everything else off, a second press sends nothing.
        # The busy page lives only until the next arrives (a locator or evaluate would wait for that
        # navigation), so the page records it the moment the press lands, presses again, and keeps it.
        check_in_url = f"{live_server.url}{reverse('tours_kiosk:check_in')}"
        sent = []
        page.on("request", lambda request: sent.append(request.method) if request.url == check_in_url else None)
        _record_press(page, "click", ['[data-kiosk-go="check-in"]', '[data-kiosk-go="book"]', "[data-kiosk-brand]"])
        page.locator('[data-kiosk-go="check-in"]').click()
        _screen(page, "check-in")
        pressed, book, brand = _recorded(page)
        assert pressed == {**IDLE, "busy": "true", "label": "Loading..."}
        assert book == brand == {**IDLE, "ariaDisabled": "true", "tabindex": "-1"}
        assert sent == ["GET"]

        # A pressed submit button (a tapped name): the same, and only its one POST goes out.
        sent.clear()
        name = f'[data-kiosk-name="{sam.pk}"]'
        _record_press(
            page, "submit", [name, "#kiosk-email", "[data-kiosk-email-form] button", "[data-kiosk-step-back]"]
        )
        page.locator(name).click()
        _screen(page, "confirm")
        expect(page.locator("[data-kiosk-confirm-who]")).to_have_text("Sam R., 4:00 PM")
        pressed, email, check_in, back = _recorded(page)
        assert pressed == {**IDLE, "busy": "true", "label": "Loading..."}
        assert email["disabled"] and check_in["disabled"]
        assert back == {**IDLE, "ariaDisabled": "true", "tabindex": "-1"}
        assert sent == ["POST"]

        # A page restored from the back/forward cache has every control back (then goes home).
        page.locator("[data-kiosk-back]").click()
        _at_home(page, live_server)
        sent.clear()
        busy, restored = page.evaluate(
            """(state) => {
                const read = eval(state);
                document.querySelector('[data-kiosk-go="check-in"]').click();
                const busy = read();
                window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
                return [busy, read()];
            }""",
            BUSY_STATE,
        )
        assert busy["busy"] == 1 and busy["labels"] == 1 and busy["off"] > 0 and busy["marked"]
        assert restored == {"busy": 0, "labels": 0, "off": 0, "marked": False}
        _at_home(page, live_server)
