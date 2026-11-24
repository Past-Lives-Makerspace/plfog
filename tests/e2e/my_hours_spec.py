"""End-to-end: My Hours (#1300, part 3), read only, in a real browser.

A Tour Guide who also leads two guilds opens ``/hours/`` (no sidebar link yet; part 5 wires the
doors) and reads the week: open hours dashed with a dot per kind, a booked tour and a booked
orientation solid in their kinds' colours, a waiting request, a class they teach and Google busy
time hatched, a day off striped. Then Every Week, Available On, Days Off, Google Calendar and the
tours only Booking Notice. Next week moves the week, and at a phone's width nothing scrolls
sideways. The data mirrors ``mockups/1300-my-hours.html``; ``CAPTURE_1300_DIR=<dir>`` saves dark
and light pictures at desktop and phone widths to compare with it. Run with ``pytest -m e2e`` on
PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from membership.models import AdminCapability, Member, OrientationAvailability, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)
from tests.tours.factories import TourBookingFactory, TourCalendarConnectionFactory, TourDayOffFactory, TourHoursFactory

EMAIL = "robin.v@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1300_DIR")
DESKTOP = {"width": 1280, "height": 1000}
PHONE = {"width": 390, "height": 844}


def _capture(page, name: str) -> None:
    """Save dark and light pictures at desktop and phone widths, when CAPTURE_1300_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", size is DESKTOP)
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light')"
                " : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(600)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1300-{name}-{size_name}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _robin() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=EMAIL, email=EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Robin Vale"
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def _seed(robin: Member, monday: date) -> None:
    """The mockup's week: Monday to Sunday of ``monday``'s week, and its lists."""
    day = {name: monday + timedelta(days=n) for n, name in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}
    printmaking = GuildFactory(name="Printmaking Guild", guild_lead=robin)
    ceramics = GuildFactory(name="Ceramics Guild", guild_lead=robin)
    beginner = OrientationTypeFactory(guild=printmaking, name="Complete Beginner to Printmaking")
    TourHoursFactory(guide=robin, weekday=0, start_time=time(11), end_time=time(18), seats=6)
    OrientationAvailabilityFactory(
        guild=printmaking, orientation_type=beginner, orienter=robin, weekday=0, start_time=time(11), end_time=time(18)
    )
    TourHoursFactory(guide=robin, weekday=2, start_time=time(12), end_time=time(17), seats=6)
    OrientationAvailabilityFactory(
        open_window=True, guild=ceramics, orienter=robin, weekday=2, start_time=time(12), end_time=time(17)
    )
    for guild, kind in ((printmaking, beginner), (ceramics, None)):
        OrientationAvailabilityFactory(
            guild=guild,
            orientation_type=kind,
            orienter=robin,
            booking_style=OrientationAvailability.BookingStyle.OPEN if kind is None else "fixed",
            weekday=6,
            cadence=OrientationAvailability.Cadence.FORTNIGHTLY,
            anchor_date=day["sun"],
            start_time=time(11),
            end_time=time(18),
        )
    TourHoursFactory(guide=robin, date=day["sat"], weekday=5, start_time=time(11), end_time=time(15), seats=6)
    TourHoursFactory(guide=robin, date=day["fri"], weekday=4, start_time=time(14), end_time=time(18), seats=6)
    TourBookingFactory(guide=robin, starts_at=_at(day["mon"], 13), ends_at=_at(day["mon"], 14), party_size=3)
    slot = OrientationSlotFactory(
        guild=printmaking,
        orientation_type=beginner,
        orienter=robin,
        starts_at=_at(day["mon"], 15),
        ends_at=_at(day["mon"], 16),
    )
    sam = MemberFactory(preferred_name="Sam Kim")
    OrientationBookingFactory(slot=slot, member=sam, status=OrientationBooking.Status.CONFIRMED)
    waiting = OrientationSlotFactory(
        guild=ceramics, orienter=robin, starts_at=_at(day["wed"], 13), ends_at=_at(day["wed"], 14, 30)
    )
    OrientationBookingFactory(slot=waiting, status=OrientationBooking.Status.REQUESTED)
    linocut = ClassOfferingFactory(instructors=[robin], title="Intro to Linocut", status=ClassOffering.Status.PUBLISHED)
    ClassSessionFactory(class_offering=linocut, starts_at=_at(day["thu"], 18, 30), ends_at=_at(day["thu"], 21))
    TourCalendarConnectionFactory(
        guide=robin,
        google_email=EMAIL,
        calendar_id="tours@group.calendar.google.com",
        busy_checked_at=timezone.now(),
        busy_spans=[[_at(day["fri"], 10).isoformat(), _at(day["fri"], 13).isoformat()]],
    )
    TourDayOffFactory(guide=robin, starts_on=day["tue"], ends_on=day["tue"], note="Out of town")
    TourDayOffFactory(guide=robin, starts_on=date(monday.year, 12, 22), ends_on=date(monday.year + 1, 1, 2))


def describe_my_hours():
    def it_reads_the_week_and_every_list_and_fits_a_phone(live_server, page, login_via_code, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
        settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"
        robin = _robin()
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        _seed(robin, monday)
        login_via_code(EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hours:my_hours')}")

        week = page.locator("[data-hours-week]")
        week.wait_for()
        legend = [text.strip() for text in week.locator("[data-hours-kind]").all_inner_texts()]
        assert legend == ["Tours", "Printmaking Guild orientations", "Ceramics Guild orientations"]
        mon = week.locator(f'[data-hours-day="{monday.isoformat()}"]')
        assert mon.locator('[data-hours-chip="open"]').inner_text().split() == ["11a", "to", "6p", "Open"]
        booked = [text.replace("\n", " ") for text in mon.locator('[data-hours-chip="booked"]').all_inner_texts()]
        assert booked == ["1p to 2p Tour · 3 visitors", "3p to 4p Printmaking Guild · Sam K."]
        tue = week.locator(f'[data-hours-day="{(monday + timedelta(days=1)).isoformat()}"]')
        assert tue.locator("[data-hours-day-off]").is_visible()
        assert tue.locator("[data-hours-chip]").count() == 0
        thu = week.locator(f'[data-hours-day="{(monday + timedelta(days=3)).isoformat()}"]')
        assert "Class: Intro to Linocut" in thu.locator('[data-hours-chip="busy"]').inner_text()
        fri = week.locator(f'[data-hours-day="{(monday + timedelta(days=4)).isoformat()}"]')
        assert "Busy in Google" in fri.locator('[data-hours-chip="busy"]').inner_text()

        weekly = page.locator("[data-hours-weekly] [data-hours-set]")
        assert weekly.count() == 3
        assert "Mon · 11:00 AM to 6:00 PM · Every week" in weekly.first.inner_text()
        assert "Every other week" in weekly.nth(2).inner_text()
        assert page.locator('[data-hours-gcal="connected"]').get_by_text(EMAIL).is_visible()
        assert page.locator("[data-hours-days-off]").get_by_text("Out of town").is_visible()
        assert page.locator("[data-hours-notice]").is_visible()
        assert page.locator("main a[href*='/edit'], main button.pl-btn--danger").count() == 0
        _capture(page, "my-hours-page")

        with page.expect_navigation():
            page.locator("[data-hours-week-next]").click()
        next_monday = monday + timedelta(weeks=1)
        page.locator(f'[data-hours-day="{next_monday.isoformat()}"]').wait_for()
        assert page.locator("[data-hours-week] h2").inner_text().strip().lower().startswith("week of")

        page.set_viewport_size(PHONE)
        page.evaluate("() => { Alpine.$data(document.body).sidebarOpen = false; }")
        page.wait_for_timeout(300)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
