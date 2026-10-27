"""End-to-end: a Tour Guide runs their tours from the Tours page's Upcoming and Past tabs (#949).

The guide opens the Tools page from the sidebar, opens the Tours card (which lands on Upcoming) and
sees both tabs empty. With bookings in place, the guide reschedules one tour through the Reschedule
modal (date strip, time, a message, Move Tour), cancels another through the confirm modal with a
message to the visitor, and marks a past tour attended on the Past tab, which swaps the row in
place with a toast. At a phone's width nothing scrolls sideways. ``CAPTURE_949_DIR=<dir>`` also
saves dark and light pictures at desktop (1100) and phone (390, sidebar drawer closed) widths of
both empty states, Upcoming, the Reschedule modal, the Cancel modal and Past. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import UTC, time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from core.models import UserProfile
from membership.models import AdminCapability, Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import TourBooking

GUIDE_EMAIL = "tour-guide-upcoming@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_949_DIR")
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}


def _capture(page, name: str) -> None:
    """Save dark and light pictures at desktop and phone widths, when CAPTURE_949_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        # The sidebar opened at desktop width stays open as a drawer over the page once narrowed;
        # a phone opens a page with it closed, so close it to show the page as a phone sees it.
        _set_drawer(page, is_open=size is DESKTOP)
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(600)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"949-{name}-{size_name}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _set_drawer(page, *, is_open: bool) -> None:
    """Open or close the hub sidebar (the body's Alpine ``sidebarOpen``) and let it settle."""
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


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


def _settled(page, selector: str) -> None:
    """Wait for ``selector`` to arrive in an htmx swap, then for htmx to finish settling it.

    htmx wires up the attributes of swapped in content (``hx-get`` on the guide select, the day
    and time buttons) only in its settle step, ``settleDelay`` (20ms) after the insert, when it
    also drops ``htmx-settling`` from the target. An interaction inside that window reaches an
    element with no htmx listener and silently does nothing: selecting Olive right after the
    modal first rendered left Gina's times on screen (#949, flaky 3 of 4 on CI).
    """
    page.locator(selector).first.wait_for()
    page.wait_for_function("() => !document.querySelector('.htmx-settling, .htmx-swapping, .htmx-request')")


def _no_sideways_scroll(page) -> None:
    page.set_viewport_size(PHONE)
    _set_drawer(page, is_open=False)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def describe_tours_upcoming_and_past():
    def it_reschedules_cancels_and_marks_tours(live_server, page, login_via_code):
        guide = _make_guide()
        day = timezone.localdate() + timedelta(days=3)
        TourHoursFactory(guide=guide, weekday=day.weekday(), start_time=time(16), end_time=time(18), seats=4)
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hub_home')}")

        # Tools, then the Tours card, which opens on Upcoming: empty for now.
        with page.expect_navigation():
            page.locator(f'.hub-sidebar__nav a[href="{reverse("hub_admin_tools")}"]').first.click()
        page.locator("[data-tool-tours]").click()
        page.wait_for_url(f"**{reverse('tours:upcoming')}")
        page.locator('[data-tours-empty="upcoming"]').wait_for()
        _capture(page, "upcoming-empty")
        page.locator('nav.pl-tabs [data-tours-tab="past"]').click()
        page.wait_for_url(f"**{reverse('tours:past')}")
        page.locator('[data-tours-empty="past"]').wait_for()
        _capture(page, "past-empty")

        four, five = local(day, 16), local(day, 17)
        sam = TourBookingFactory(
            guide=guide,
            starts_at=four,
            party_size=2,
            visitor_name="Sam Rivera",
            visitor_email="sam@example.com",
            visitor_phone="503 555 0142",
        )
        lee = TourBookingFactory(
            guide=guide, starts_at=five, party_size=1, visitor_name="Lee Park", visitor_email="lee@example.com"
        )
        visitor = User.objects.create_user("pat-visitor", "pat@example.com")
        started = timezone.now() - timedelta(days=1)
        pat = TourBookingFactory(
            guide=guide,
            starts_at=started,
            ends_at=started + timedelta(hours=1),
            visitor_name="Pat Past",
            visitor_email="pat@example.com",
            user=visitor,
        )

        page.goto(f"{live_server.url}{reverse('tours:upcoming')}")
        page.locator(f'[data-tour-booking="{sam.pk}"]').wait_for()
        assert page.locator(f'[data-tour-booking="{pat.pk}"]').count() == 0
        _capture(page, "upcoming")
        _no_sideways_scroll(page)

        # Reschedule Sam from 4:00 to 5:00 through the modal.
        page.locator(f'[data-tour-reschedule="{sam.pk}"]').click()
        modal = page.locator("#tour-reschedule-body")
        _settled(page, f'#tour-reschedule-body [data-tour-move-time="{_key(guide, five)}"]')
        assert modal.locator(f'[data-tour-move-time="{_key(guide, four)}"]').count() == 0
        _capture(page, "reschedule-modal")
        modal.locator(f'[data-tour-move-time="{_key(guide, five)}"]').click()
        _settled(page, f'#tour-reschedule-body [data-tour-move-chosen="{_key(guide, five)}"]')
        modal.locator('textarea[name="message"]').fill("Our 4:00 ran long, see you at 5:00.")
        _capture(page, "reschedule-confirm")
        mail.outbox = []
        with page.expect_navigation():
            modal.locator("[data-tour-move-save]").click()
        page.locator(".plt-msg", has_text="Sam Rivera's tour moved to").first.wait_for()
        sam.refresh_from_db()
        assert (sam.starts_at, sam.sequence) == (five, 1)
        assert any(message.to == ["sam@example.com"] and "Our 4:00 ran long" in message.body for message in mail.outbox)

        # Cancel Lee through the confirm modal, with a message.
        page.locator(f'[data-tour-cancel="{lee.pk}"]').click()
        dialog = page.get_by_role("dialog").filter(has_text="Cancel This Tour?")
        dialog.wait_for(state="visible")
        dialog.locator(f"#tour-cancel-{lee.pk}-note").fill("The shop is closed that day, sorry.")
        _capture(page, "cancel-modal")
        mail.outbox = []
        with page.expect_navigation():
            dialog.get_by_role("button", name="Cancel Tour", exact=True).click()
        page.locator(".plt-msg", has_text="Lee Park's tour on").first.wait_for()
        assert page.locator(f'[data-tour-booking="{lee.pk}"]').count() == 0
        lee.refresh_from_db()
        assert lee.status == TourBooking.Status.CANCELLED
        assert any(
            message.to == ["lee@example.com"] and "The shop is closed that day" in message.body
            for message in mail.outbox
        )

        # Mark Pat attended on the Past tab: the row swaps in place with a toast.
        page.locator('nav.pl-tabs [data-tours-tab="past"]').click()
        page.wait_for_url(f"**{reverse('tours:past')}")
        row = page.locator(f'[data-tour-booking="{pat.pk}"]')
        row.wait_for()
        _capture(page, "past")
        _settled(page, f'[data-tour-booking="{pat.pk}"] [data-tour-mark="attended"]')
        row.locator('[data-tour-mark="attended"]').click()
        page.locator(f'[data-tour-booking="{pat.pk}"] [data-tour-booking-status="attended"]').wait_for()
        page.locator(".plt-msg", has_text="Pat Past marked attended.").first.wait_for()
        pat.refresh_from_db()
        assert pat.status == TourBooking.Status.ATTENDED
        assert UserProfile.objects.get(user=visitor).completed_tour_at is not None
        _capture(page, "past-marked")
        _no_sideways_scroll(page)


ADMIN_EMAIL = "tour-admin-upcoming@example.com"


def _make_admin() -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=ADMIN_EMAIL, email=ADMIN_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Ada Admin"
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["status", "full_legal_name", "fog_role"])
    return member


def _other_guide(name: str) -> Member:
    member = MemberFactory(full_legal_name=name)
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def describe_admin_view():
    def it_filters_by_guide_and_reassigns_through_the_guide_picker(live_server, page, login_via_code):
        _make_admin()
        gina, olive = _other_guide("Gina Guide"), _other_guide("Olive Other")
        day = timezone.localdate() + timedelta(days=3)
        for guide in (gina, olive):
            TourHoursFactory(guide=guide, weekday=day.weekday(), start_time=time(16), end_time=time(18), seats=4)
        four = local(day, 16)
        sam = TourBookingFactory(
            guide=gina, starts_at=four, party_size=2, visitor_name="Sam Rivera", visitor_email="sam@example.com"
        )
        oli = TourBookingFactory(
            guide=olive, starts_at=four, party_size=1, visitor_name="Oli Visitor", visitor_email="oli@example.com"
        )
        login_via_code(ADMIN_EMAIL)
        page.set_viewport_size(DESKTOP)

        # Every guide's tours, then one guide's through the filter.
        page.goto(f"{live_server.url}{reverse('tours:upcoming')}")
        page.locator(f'[data-tour-booking="{oli.pk}"]').wait_for()
        assert page.locator(f'[data-tour-booking="{sam.pk}"]').count() == 1
        _capture(page, "admin-upcoming")
        with page.expect_navigation():
            page.locator("#tours-guide-filter").select_option(str(gina.pk))
        page.locator(f'[data-tour-booking="{sam.pk}"]').wait_for()
        assert page.locator(f'[data-tour-booking="{oli.pk}"]').count() == 0
        _capture(page, "admin-filtered")
        _no_sideways_scroll(page)

        # The modal's guide picker shows Olive's times; picking one reassigns Sam to her.
        page.locator(f'[data-tour-reschedule="{sam.pk}"]').click()
        modal = page.locator("#tour-reschedule-body")
        _settled(page, "#tour-reschedule-body [data-tour-move-guide]")
        modal.locator("[data-tour-move-guide]").select_option(str(olive.pk))
        _settled(page, f'#tour-reschedule-body [data-tour-move-time="{_key(olive, four)}"]')
        _capture(page, "admin-reschedule-guide-picker")
        modal.locator(f'[data-tour-move-time="{_key(olive, four)}"]').click()
        _settled(page, "#tour-reschedule-body [data-tour-move-save]")
        with page.expect_navigation():
            modal.locator("[data-tour-move-save]").click()
        page.locator(".plt-msg", has_text="Sam Rivera's tour moved to").first.wait_for()
        sam.refresh_from_db()
        assert sam.guide == olive
