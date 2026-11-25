"""End-to-end: editing My Hours (#1300, part 4b) in a real browser.

A Tour Guide who leads a guild adds a set of hours with + Add Hours: every kind starts ticked, and
the row saves itself once Day, From and Until are picked (the Every Week pill says Saved). Changing
Until saves; ticking the orientation off saves and the summary drops its chip; Delete asks first,
then saves the set away. With the mockup's data, the first set opened is the approved ``?view=edit``:
``CAPTURE_1300_DIR=<dir>`` saves it dark and light at desktop and phone widths. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import time, timedelta

from django.urls import reverse
from django.utils import timezone

from hours.models import DayOff, Hours
from membership.models import OrientationAvailability
from tests.e2e.my_hours_spec import DESKTOP, EMAIL, PHONE, _at, _capture, _robin, _seed
from tests.membership.factories import GuildFactory, GuildOrientationSettingsFactory, OrientationTypeFactory
from tests.tours.factories import TourBookingFactory
from tours.models import FacilityTourSettings, TourBooking, TourHours


def _saved_past(page, count: int) -> None:
    """Wait until the Every Week pill reads Saved after at least ``count`` saves (the queue drained)."""
    page.wait_for_function(
        "(n) => { const pill = document.querySelector('[data-tour-save-status=\"weekly\"]');"
        " const root = document.querySelector('[data-guild-autosave]').plGuildAutosave;"
        " return !!pill && root.pending === 0 && Number(pill.dataset.saves) >= n"
        " && pill.textContent.trim() === 'Saved' && pill.offsetParent !== null; }",
        arg=count,
    )


def _saves(page) -> int:
    page.wait_for_function("() => document.querySelector('[data-guild-autosave]').plGuildAutosave.pending === 0")
    return int(page.locator('[data-tour-save-status="weekly"]').get_attribute("data-saves") or 0)


def describe_my_hours_editing():
    def it_adds_changes_and_deletes_a_set_of_hours(live_server, page, login_via_code):
        robin = _robin()
        guild = GuildFactory(name="Printmaking Guild", guild_lead=robin)
        GuildOrientationSettingsFactory(guild=guild)
        OrientationTypeFactory(guild=guild, name="Complete Beginner to Printmaking")
        login_via_code(EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hours:my_hours')}")
        page.locator("[data-hours-weekly-empty]").wait_for()

        page.locator("[data-hours-add]").click()
        row = page.locator("[data-hours-weekly] [data-hours-set]").first
        row.locator("[data-hours-set-body]").wait_for(state="visible")
        assert row.locator('input[name="weekly-0-tours-on"]').is_checked()
        assert row.locator(f'input[name="weekly-0-guild-{guild.pk}-on"]').is_checked()
        row.locator('select[name="weekly-0-weekday"]').select_option("0")
        row.locator('select[name="weekly-0-start_time"]').select_option("11:00")
        before = _saves(page)
        assert not Hours.objects.exists()
        row.locator('select[name="weekly-0-end_time"]').select_option("18:00")
        _saved_past(page, before + 1)
        hours = Hours.objects.get()
        assert hours.tour_hours.count() == 1 and hours.orientation_rules.count() == 1
        assert row.locator('input[name="weekly-0-id"]').input_value() == str(hours.pk)
        row.locator("[data-hours-set-when]").get_by_text("Mon · 11:00 AM to 6:00 PM · Every week").wait_for()

        # Until moves, and the orientation ticked off leaves the summary.
        before = _saves(page)
        row.locator('select[name="weekly-0-end_time"]').select_option("17:00")
        _saved_past(page, before + 1)
        assert TourHours.objects.get().end_time == time(17)
        before = _saves(page)
        row.locator(f'[data-hours-kind="guild-{guild.pk}"] .pl-toggle').click()
        _saved_past(page, before + 1)
        assert not OrientationAvailability.objects.exists()
        seats = FacilityTourSettings.load().default_seats
        assert row.locator("[data-hours-set-kinds] .pl-hours-set__kind").all_inner_texts() == [
            f"Tours · 1 hour each · {seats} seats"
        ]

        page.reload()
        saved = page.locator("[data-hours-weekly] [data-hours-set]").first
        saved.locator("[data-hours-set-when]").get_by_text("Mon · 11:00 AM to 5:00 PM · Every week").wait_for()
        assert not saved.locator("[data-hours-set-body]").is_visible()

        # Delete asks first, then saves the set away.
        saved.locator("[data-hours-set-edit]").click()
        saved.locator("[data-hours-set-delete]").click()
        dialog = page.get_by_role("dialog").filter(has_text="Delete these hours?")
        dialog.wait_for(state="visible")
        before = _saves(page)
        dialog.get_by_role("button", name="Delete", exact=True).click()
        _saved_past(page, before + 1)
        page.locator("[data-hours-weekly-empty]").wait_for()
        assert not Hours.objects.exists() and not TourHours.objects.exists()

        page.set_viewport_size(PHONE)
        page.evaluate("() => { Alpine.$data(document.body).sidebarOpen = false; }")
        page.wait_for_timeout(300)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")

    def it_opens_a_set_as_the_mockup_draws_it(live_server, page, login_via_code, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
        settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"
        robin = _robin()
        today = timezone.localdate()
        _seed(robin, today - timedelta(days=today.weekday()))
        OrientationTypeFactory(guild=GuildFactory(name="Ceramics Guild"), name="Wheel Throwing")
        login_via_code(EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hours:my_hours')}")
        first = page.locator("[data-hours-weekly] [data-hours-set]").first
        first.locator("[data-hours-set-edit]").click()
        first.locator("[data-hours-set-body]").wait_for(state="visible")
        kinds = first.locator("[data-hours-kind]")
        assert [kinds.nth(n).get_attribute("data-kind-label") for n in range(kinds.count())] == [
            "Tours",
            "Printmaking Guild orientations",
            "Ceramics Guild orientations",
        ]
        assert not first.locator('[data-hours-kind^="guild-"] input[type="checkbox"]').last.is_checked()
        _capture(page, "my-hours-edit")


def describe_days_off():
    def it_adds_a_day_off_listing_what_stays_booked(live_server, page, login_via_code, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
        settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"
        robin = _robin()
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        _seed(robin, monday - timedelta(weeks=1))
        TourBookingFactory(guide=robin, starts_at=_at(monday, 13), ends_at=_at(monday, 14), party_size=3)
        login_via_code(EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hours:my_hours')}")
        page.locator("[data-hours-day-off-add]").click()
        modal = page.locator("[data-hours-day-off-form]")
        modal.wait_for(state="visible")
        modal.locator('input[name="day_off-starts_on"]').fill(monday.isoformat())
        modal.locator('input[name="day_off-starts_on"]').dispatch_event("change")
        impact = modal.locator("[data-hours-day-off-impact]")
        impact.get_by_text("1 booking that day stays booked:").wait_for()
        assert "Tour at 1:00 PM, 3 visitors" in impact.inner_text()
        _capture(page, "my-hours-dayoff", full_page=False)
        with page.expect_navigation():
            modal.locator("[data-hours-day-off-save]").click()
        page.locator(f'[data-hours-day-off-row="{DayOff.objects.get(starts_on=monday).pk}"]').wait_for()
        assert TourBooking.objects.filter(starts_at=_at(monday, 13)).exists()
