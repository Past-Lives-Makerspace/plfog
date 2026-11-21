"""End-to-end: every Tour Guide sees the whole picture on the Tours page (#1274).

A guide opens the Calendar tab and sees every guide's hours and tours with the days nobody covers,
and Add My Hours on a gap opens Add Dates with that day picked. On Upcoming, every guide's tours sit
in one table: the guide's own row's "..." offers Notes, Edit Guide or Time and Cancel Tour, another
guide's offers Notes alone. The guide adds a note and emails it to the visitor; the Notes modal opens
again with the note marked Emailed to the visitor. The Edit modal offers the other guides. Nothing
scrolls sideways on a phone. ``CAPTURE_1274_DIR=<dir>`` also saves dark and light pictures at desktop
and phone widths, and the three guidance shots under ``<dir>/guidance``. Run with ``pytest -m e2e``
on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member, PermissionSetup
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory
from tests.tours.conftest import make_guide
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import FacilityTourSettings, TourBooking

GUIDE_EMAIL = "tour-guide-transparency@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1274_DIR")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}


def _set_drawer(page, *, is_open: bool) -> None:
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


def _capture(page, name: str, *, full_page: bool = True, after_resize=None) -> None:  # noqa: ANN001
    """Dark and light pictures at desktop and phone widths, when CAPTURE_1274_DIR is set.

    ``after_resize`` runs at each width once the drawer settled: a resize closes a row menu, so a
    picture of one opens it again there.
    """
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        _set_drawer(page, is_open=size is DESKTOP)
        if after_resize is not None:
            after_resize()
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(500)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1274-{name}-{size_name}-{theme}.png"), full_page=full_page)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _guidance(page, step: int, target: str | None = None) -> None:
    if CAPTURE_DIR:
        shoot(page, Path(CAPTURE_DIR) / "guidance" / f"step-{step}.png", target=target)
        page.set_viewport_size(DESKTOP)


def _no_sideways_scroll(page) -> None:
    page.set_viewport_size(PHONE)
    _set_drawer(page, is_open=False)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _make_guide() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=GUIDE_EMAIL, email=GUIDE_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Gina Guide"
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def _open_row_menu(page, pk: int) -> None:
    page.locator(f'[data-tour-booking="{pk}"] .pl-row-menu__trigger').click()


def describe_tours_for_every_guide():
    def it_shows_the_calendar_the_table_and_notes(live_server, page, login_via_code):
        house = FacilityTourSettings.load()
        house.booking_horizon_days = 14
        house.save()
        gina = _make_guide()
        olive = make_guide(full_legal_name="Olive Other")
        amber = make_guide(full_legal_name="Amber Capwell")
        today = timezone.localdate()
        saturday = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
        TourHoursFactory(guide=gina, weekday=saturday.weekday(), start_time=time(11), end_time=time(15), seats=6)
        TourHoursFactory(guide=olive, weekday=(saturday - timedelta(days=2)).weekday(), start_time=time(16))
        dated = saturday + timedelta(days=1)
        TourHoursFactory(
            guide=amber, weekday=dated.weekday(), date=dated, start_time=time(12), end_time=time(14), seats=4
        )
        sam = TourBookingFactory(
            guide=gina,
            starts_at=local(saturday, 11),
            party_size=3,
            visitor_name="Sam Rivera",
            visitor_email="sam@example.com",
            visitor_phone="503 555 0142",
        )
        oli = TourBookingFactory(
            guide=olive, starts_at=local(saturday - timedelta(days=2), 16), party_size=1, visitor_name="Oli Visitor"
        )
        TourBookingFactory(
            guide=amber,
            starts_at=local(dated, 12),
            party_size=2,
            visitor_name="Rae Quest",
            status=TourBooking.Status.PENDING,
        )
        # The Get Set Up card is #1167's; hidden here so the pictures show this change.
        PermissionSetup.objects.update_or_create(
            member=gina, permission=AdminCapability.Capability.TOUR_GUIDE, defaults={"hidden_at": timezone.now()}
        )
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)

        # The Calendar tab: every guide's hours and tours, and the days nobody covers.
        page.goto(f"{live_server.url}{reverse('tours:calendar')}")
        page.locator("[data-tours-gaps]").wait_for()
        assert page.locator("[data-tours-gap]").count() > 0
        page.locator(".pl-calendar-filter", has_text="Olive Other").wait_for()
        _capture(page, "calendar")
        _guidance(page, 1, target="tours.gaps")
        _no_sideways_scroll(page)

        # Add My Hours on a gap: Add Dates opens with that day picked.
        gap = page.locator("[data-tours-gap]").first
        day = gap.get_attribute("data-tours-gap")
        with page.expect_navigation():
            gap.locator("[data-tours-gap-add]").click()
        page.locator("[data-tour-dates-panel]").wait_for(state="visible")
        page.locator(f'[data-tour-date="{day}"][aria-pressed="true"]').wait_for()
        _capture(page, "add-from-gap", full_page=False)

        # Upcoming: every guide's tours; the guide acts on their own, reads the others.
        page.goto(f"{live_server.url}{reverse('tours:upcoming')}")
        page.locator(f'[data-tour-booking="{oli.pk}"]').wait_for()
        _capture(page, "upcoming")
        _no_sideways_scroll(page)
        _open_row_menu(page, oli.pk)
        menu = page.locator(f'[data-tour-booking="{oli.pk}"] .pl-row-menu__menu')
        menu.locator(f'[data-tour-notes="{oli.pk}"]').wait_for()
        assert menu.locator("[data-tour-reschedule]").count() == 0
        page.keyboard.press("Escape")
        _open_row_menu(page, sam.pk)
        page.locator(f'[data-tour-reschedule="{sam.pk}"]').wait_for()
        _guidance(page, 2)

        def reopen() -> None:
            page.wait_for_timeout(400)  # a resize closes the menu; let its event land first
            page.keyboard.press("Escape")
            page.locator(f'[data-tour-booking="{sam.pk}"]').scroll_into_view_if_needed()
            _open_row_menu(page, sam.pk)
            page.locator(f'[data-tour-reschedule="{sam.pk}"]').wait_for()

        _capture(page, "row-menu", full_page=False, after_resize=reopen)

        # The Edit modal offers every guide's times.
        reopen()
        page.locator(f'[data-tour-reschedule="{sam.pk}"]').click()
        page.locator("#tour-reschedule-body [data-tour-move-guide]").wait_for()
        page.wait_for_function("() => !document.querySelector('.htmx-settling, .htmx-swapping, .htmx-request')")
        _capture(page, "edit-modal", full_page=False)
        page.keyboard.press("Escape")

        # Notes: a note to the other guides, emailed to the visitor too.
        _open_row_menu(page, sam.pk)
        page.locator(f'[data-tour-notes="{sam.pk}"]').click()
        body = page.locator("#tour-notes-body")
        body.locator("[data-thread-reply]").wait_for()
        body.locator('textarea[name="body"]').fill(
            "Sam's group uses a wheelchair. Start at the ramp by the loading door."
        )
        body.locator("label.pl-toggle").click()
        assert body.locator('input[name="email_visitor"]').is_checked()
        mail.outbox = []
        with page.expect_navigation():
            body.locator("[data-thread-send]").click()
        page.locator("#tour-notes-body [data-thread-marker]", has_text="Emailed to the visitor").wait_for()
        assert any(message.to == ["sam@example.com"] for message in mail.outbox)
        assert sam.notes.get().sent_to_visitor
        _guidance(page, 3)
        _capture(page, "notes", full_page=False)
