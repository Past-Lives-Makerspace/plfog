"""End-to-end: the Edit Hours row says its recurrence in plain words before a save (#430).

A guild lead opens Manage Bookings, presses Edit Hours under Your Hours, and changes how a row
repeats. The line under the row follows every change in the browser (plHoursRow,
``static/js/orientation_hours_recurrence.js``): every 3 weeks, then by the month with Last of
the month, which shows only under a month based cadence. Saved, the guild's Orientations page
reads the same words (``OrientationAvailability.cadence_display``). Run with ``pytest -m e2e`` on
PostgreSQL.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Member, OrientationAvailability
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationTypeFactory,
)

LEAD_EMAIL = "hours-recurrence-lead@example.com"


def _lead(login_via_code) -> Member:
    MembershipPlanFactory()
    login_via_code(LEAD_EMAIL)
    member = get_user_model().objects.get(username=LEAD_EMAIL).member
    member.full_legal_name = "Lena Lead"
    member.save(update_fields=["full_legal_name"])
    return member


def _last_thursday_next_month() -> date:
    """The last Thursday of next month: always ahead, so the line carries its "from" date."""
    today = timezone.localdate()
    first_of_month_after = date(today.year + (today.month + 1) // 12, (today.month + 1) % 12 + 1, 1)
    last_day = first_of_month_after - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - 3) % 7)


def _short(day: date) -> str:
    return f"{day:%b} {day.day}"


def describe_the_edit_hours_row():
    def it_says_the_recurrence_in_plain_words_before_and_after_a_save(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        lead = _lead(login_via_code)
        guild = GuildFactory(name="Tech Guild", guild_lead=lead)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        laser = OrientationTypeFactory(guild=guild, name="Laser Basics")
        rule = OrientationAvailabilityFactory(
            guild=guild, orientation_type=laser, orienter=lead, weekday=OrientationAvailability.Weekday.THURSDAY
        )
        page.goto(f"{live_server.url}{reverse('hub_manage_bookings')}")
        page.locator("[data-edit-hours]").first.click()
        row = page.locator("#modal-rule-rows .pl-hours-row").first
        summary = row.locator("[data-hours-summary]")
        expect(summary).to_have_text("Every Thursday, 6:00 PM to 7:00 PM")
        last_switch = row.locator("[data-hours-last]")
        expect(last_switch).to_be_hidden()

        anchor = _last_thursday_next_month()
        row.locator('select[name$="-cadence"]').select_option("every_3_weeks")
        row.locator('input[name$="-anchor_date"]').fill(anchor.isoformat())
        expect(summary).to_have_text(f"Every 3 weeks on Thursday from {_short(anchor)}, 6:00 PM to 7:00 PM")
        expect(last_switch).to_be_hidden()

        ordinal = ("1st", "2nd", "3rd", "4th", "5th")[(anchor.day - 1) // 7]
        row.locator('select[name$="-cadence"]').select_option("monthly")
        expect(last_switch).to_be_visible()
        expect(summary).to_have_text(f"Every month on the {ordinal} Thursday from {_short(anchor)}, 6:00 PM to 7:00 PM")
        last_switch.locator("label.pl-toggle").click()
        expect(summary).to_have_text(f"Every month on the last Thursday from {_short(anchor)}, 6:00 PM to 7:00 PM")

        # The save redirects to the guild's Orientations page, whose schedule reads the saved rule.
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
        expect(page.locator(".pl-orient-overview__rules li").first).to_contain_text(
            f"Every month on the last Thursday from {_short(anchor)}"
        )
        rule.refresh_from_db()
        assert (rule.cadence, rule.anchor_date, rule.last_of_month) == ("monthly", anchor, True)
