"""End-to-end: a guide's reschedule asks the visitor first, and the guide hears the answer (#1283).

Gina reschedules Sam's 4:00 tour to 5:00 from the Reschedule modal with a note. The tour stays at
4:00, and its row on Upcoming and its entry on the Calendar tab say Waiting for the Visitor with the
proposed time. Sam opens the
email's Accept New Time link on the tours site, sees the proposal with both answers, and accepts:
the tour moves to 5:00, Sam gets the moved email with the invite, and Gina gets New tour time
accepted while her row no longer waits. ``CAPTURE_1283_DIR=<dir>`` saves the modal, the waiting row,
the visitor's page and the email, dark, at desktop (1280x800) and phone (390) widths. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, time, timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import AdminCapability, Member
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import TourBooking

GUIDE_EMAIL = "tour-guide-ask-first@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1283_DIR")
#: Where the What's New shots go (docs/GUIDANCE.md), step-1.png to step-3.png at the standard frame.
GUIDANCE_DIR = os.environ.get("GUIDANCE_1283_DIR")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}


def _capture(page, name: str, focus: str, *, public: bool = False, whole_phone: bool = False) -> None:
    """Dark, at desktop and phone widths with ``focus`` scrolled into view, when CAPTURE_1283_DIR is set.

    The visitor's page is taken whole at phone width; a hub page is the viewport (its modal is fixed).

    The hub is dark with no ``data-theme``; the public tours site needs ``data-theme="dark"``.
    """
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    page.evaluate("() => document.querySelectorAll('.plt-toast').forEach((el) => el.remove())")
    page.evaluate(
        "(pub) => pub ? document.documentElement.setAttribute('data-theme', 'dark') : document.documentElement.removeAttribute('data-theme')",
        public,
    )
    for label, viewport in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(viewport)
        page.evaluate(
            "() => window.Alpine && document.body._x_dataStack && (Alpine.$data(document.body).sidebarOpen = false)"
        )
        page.wait_for_timeout(400)
        page.locator(focus).first.scroll_into_view_if_needed()
        page.screenshot(
            path=str(Path(CAPTURE_DIR) / f"1283-{name}-{label}-dark.png"),
            full_page=(public or whole_phone) and label == "phone",
        )
    page.set_viewport_size(DESKTOP)


def _guidance_shot(page, step: int, selector: str) -> None:
    """Step ``step`` of the fragment's shots, ``selector`` ringed in gold, when GUIDANCE_1283_DIR is set.

    The ring hangs off ``data-guidance-target``, so the control is marked for the shot alone.
    """
    if not GUIDANCE_DIR:
        return
    page.evaluate("() => document.querySelectorAll('.plt-toast').forEach((el) => el.remove())")
    control = page.locator(selector).first
    control.evaluate("(el) => el.setAttribute('data-guidance-target', 'shot.step')")
    control.scroll_into_view_if_needed()
    shoot(page, Path(GUIDANCE_DIR) / f"step-{step}.png", target="shot.step")
    control.evaluate("(el) => el.removeAttribute('data-guidance-target')")


def _settled(page, selector: str) -> None:
    """Wait for ``selector`` in an htmx swap and for htmx to finish settling it (see tours_upcoming_spec)."""
    page.locator(selector).first.wait_for()
    page.wait_for_function("() => !document.querySelector('.htmx-settling, .htmx-swapping, .htmx-request')")


def _make_guide() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=GUIDE_EMAIL, email=GUIDE_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Gina Guide"
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def _key(guide: Member, starts_at) -> str:  # noqa: ANN001
    return f"{guide.pk}-{starts_at.astimezone(UTC):%Y%m%dT%H%MZ}"


def _tours_site(live_server, settings) -> str:
    """The tours site answers on 127.0.0.1, the members site on localhost, both this live server."""
    server = urlparse(live_server.url)
    tours_url = f"http://127.0.0.1:{server.port}"
    settings.TOURS_HOSTS = ["127.0.0.1"]
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "127.0.0.1"]
    settings.TOURS_BASE_URL = tours_url
    return tours_url


def describe_ask_the_visitor_first():
    def it_waits_for_the_visitor_who_accepts_and_the_guide_hears(live_server, page, login_via_code, settings):
        _tours_site(live_server, settings)
        guide = _make_guide()
        day = timezone.localdate() + timedelta(days=3)
        TourHoursFactory(guide=guide, weekday=day.weekday(), start_time=time(16), end_time=time(18), seats=4)
        four, five = local(day, 16), local(day, 17)
        sam = TourBookingFactory(
            guide=guide, starts_at=four, party_size=2, visitor_name="Sam Rivera", visitor_email="sam@example.com"
        )
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:upcoming')}")
        page.locator(f'[data-tour-booking="{sam.pk}"]').wait_for()

        # Gina asks Sam to move to 5:00, with a note.
        page.locator(f'[data-tour-booking="{sam.pk}"] .pl-row-menu__trigger').click()
        page.locator(f'[data-tour-reschedule="{sam.pk}"]').click()
        modal = page.locator("#tour-reschedule-body")
        _settled(page, f'#tour-reschedule-body [data-tour-move-time="{_key(guide, five)}"]')
        modal.locator(f'[data-tour-move-time="{_key(guide, five)}"]').click()
        _settled(page, "#tour-reschedule-body [data-tour-move-ask]")
        modal.locator('textarea[name="message"]').fill("Our 4:00 runs long that day. Would 5:00 work?")
        _capture(page, "modal", "[data-tour-move-save]")
        _guidance_shot(page, 1, "[data-tour-move-save]")
        mail.outbox = []
        with page.expect_navigation():
            modal.locator("[data-tour-move-save]").click()
        page.locator(".plt-msg", has_text="We asked Sam Rivera to move to").first.wait_for()
        row = page.locator(f'[data-tour-proposal="{sam.pk}"]')
        row.wait_for()
        assert "Waiting for the Visitor" in row.inner_text()
        sam.refresh_from_db()
        assert (sam.starts_at, sam.proposed_starts_at) == (four, five)
        _capture(page, "upcoming-waiting", f'[data-tour-booking="{sam.pk}"]')
        _guidance_shot(page, 2, f'[data-tour-proposal="{sam.pk}"]')
        page.goto(f"{live_server.url}{reverse('tours:calendar')}")
        # A tour three days out may fall in next week, so the month always holds it.
        page.get_by_role("button", name="Month", exact=True).click()
        waiting = ".pl-calendar-list__guild:visible:has-text('Waiting for the Visitor, asked to move to')"
        page.locator(waiting).first.wait_for()
        # expect() retries: the month grid turns visible a beat after the list, and a one-shot count
        # taken in that beat read 0 under Playwright 1.65.
        expect(page.locator(".pl-calendar-grid--month:visible", has_text="Sam R. (2) · Waiting")).to_have_count(1)
        _capture(page, "calendar-waiting", waiting, whole_phone=True)
        page.goto(f"{live_server.url}{reverse('tours:upcoming')}")

        (asked,) = [message for message in mail.outbox if message.to == ["sam@example.com"]]
        assert asked.subject.startswith("Can your Past Lives tour move to")
        if CAPTURE_DIR:
            email_page = page.context.new_page()
            email_page.set_content(str(asked.alternatives[0][0]))
            for label, viewport in (("desktop", DESKTOP), ("phone", PHONE)):
                email_page.set_viewport_size(viewport)
                email_page.screenshot(path=str(Path(CAPTURE_DIR) / f"1283-email-{label}.png"), full_page=True)
            email_page.close()
        accept_url = re.search(r"^Accept the new time: (\S+)$", asked.body, re.MULTILINE)
        assert accept_url is not None

        # Sam opens Accept New Time from the email: the page asks, and nothing moves until the press.
        visitor = page.context.new_page()
        visitor.set_viewport_size(DESKTOP)
        visitor.goto(accept_url.group(1))
        visitor.locator("[data-tour-proposal]").wait_for()
        sam.refresh_from_db()
        assert sam.starts_at == four
        _capture(visitor, "visitor-answer", "[data-tour-answers]", public=True)
        _guidance_shot(visitor, 3, "[data-tour-answers]")
        mail.outbox = []
        with visitor.expect_navigation():
            visitor.locator('[data-tour-answer="accept"]').click()
        visitor.locator("[data-tour-confirmed]").wait_for()
        assert visitor.locator("[data-tour-proposal]").count() == 0
        sam.refresh_from_db()
        assert (sam.starts_at, sam.status, sam.has_open_proposal) == (five, TourBooking.Status.BOOKED, False)
        assert any(message.subject.startswith("Your Past Lives tour moved to") for message in mail.outbox)
        assert any(
            message.to == [GUIDE_EMAIL] and message.subject.startswith("New tour time accepted")
            for message in mail.outbox
        )
        visitor.close()

        page.reload()
        page.locator(f'[data-tour-booking="{sam.pk}"]').wait_for()
        assert page.locator(f'[data-tour-proposal="{sam.pk}"]').count() == 0
        assert "5:00 PM" in page.locator(f'[data-tour-booking="{sam.pk}"] [data-tour-booking-when]').inner_text()
