"""End-to-end: Record Orientation on the Orientations page's Bookings tab (#630) in a real browser.

A guild lead opens the tab, clicks + Record Orientation beside + Add Member, picks a member and
one of their guild's orientations from the datalist pickers, keeps the defaults (today, run by
them) and saves; they land back on the tab's Oriented list with the new record listed and the
member counted as oriented. At 375px the two buttons and the modal fit with no sideways scroll.
Waits are on what the page shows, never a snapshot after it. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Member, OrientationRecord, OrientationType
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

LEAD_EMAIL = "record-orientation-lead@example.com"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _lead_world(login_via_code: Any) -> tuple[Member, OrientationType, Member]:
    """Sign a guild lead in; their guild has one orientation type and an upcoming slot (so Add Member shows too)."""
    MembershipPlanFactory()
    login_via_code(LEAD_EMAIL)
    lead = get_user_model().objects.get(username=LEAD_EMAIL).member
    guild = GuildFactory(name="Recording Woodshop", guild_lead=lead)
    orientation_type = OrientationTypeFactory(guild=guild, name="Bench Basics")
    starts = timezone.now() + timedelta(days=2)
    OrientationSlotFactory(
        guild=guild, orientation_type=orientation_type, starts_at=starts, ends_at=starts + timedelta(hours=1)
    )
    target = MemberFactory(full_legal_name="Remy Recordable", status=Member.Status.ACTIVE)
    return lead, orientation_type, target


def _record(page: Any, orientation_type: OrientationType) -> None:
    page.locator("[data-bookings-record]").click()
    dialog = page.get_by_role("dialog").filter(has_text="Record an Orientation")
    expect(dialog).to_be_visible()
    dialog.locator("#orientation-record-member").fill("Remy Recordable")
    dialog.locator("#orientation-record-orientation").fill(str(orientation_type))
    dialog.get_by_role("button", name="Record Orientation").click()


def describe_record_orientation():
    def it_records_from_the_modal_and_lands_on_the_tab_with_the_row(live_server, page, login_via_code):
        lead, orientation_type, target = _lead_world(login_via_code)
        tab = f"{live_server.url}{reverse('hub_orientations')}?view=bookings"
        page.goto(tab)
        expect(page.locator("[data-bookings-add-member]")).to_be_visible()
        expect(page.locator("[data-bookings-record]")).to_have_text("+ Record Orientation")

        _record(page, orientation_type)

        expect(page).to_have_url(f"{tab}&oriented=yes")
        records = page.locator(".pl-orient-records")
        row = records.locator("[data-record-row]").filter(has_text="Remy Recordable")
        expect(row).to_be_visible()
        expect(row).to_contain_text("Bench Basics")
        record = OrientationRecord.objects.get(member=target)
        assert record.orientation_type == orientation_type
        assert record.completed_on == timezone.localdate()
        assert record.oriented_by == lead
        assert record.recorded_by == lead.user
        assert orientation_type.pk in target.completed_orientation_type_ids([orientation_type])

        # The same lead can take it back from the row's menu.
        row.get_by_role("button", name="Actions for Remy Recordable Bench Basics").click()
        row.get_by_role("menuitem", name="Remove Record").click()
        confirm = page.get_by_role("dialog").filter(has_text="Remove This Record?")
        expect(confirm).to_be_visible()
        confirm.get_by_role("button", name="Remove Record").click()
        expect(page.locator(f'[data-record-row="{record.pk}"]')).to_have_count(0)
        expect(page).to_have_url(f"{tab}&oriented=yes")
        assert not OrientationRecord.objects.filter(pk=record.pk).exists()

    def it_fits_a_phone_with_no_sideways_scroll(live_server, page, login_via_code):
        page.set_viewport_size({"width": 375, "height": 812})
        _lead, orientation_type, target = _lead_world(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}?view=bookings")
        expect(page.locator("[data-bookings-record]")).to_be_in_viewport()
        expect(page.locator("[data-bookings-add-member]")).to_be_in_viewport()
        page.wait_for_function(NO_SIDEWAYS_SCROLL)

        page.locator("[data-bookings-record]").click()
        dialog = page.get_by_role("dialog").filter(has_text="Record an Orientation")
        expect(dialog.get_by_role("button", name="Record Orientation")).to_be_visible()
        page.wait_for_function(NO_SIDEWAYS_SCROLL)
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_be_hidden()

        _record(page, orientation_type)
        row = page.locator(".pl-orient-records [data-record-row]").filter(has_text="Remy Recordable")
        expect(row).to_be_visible()
        trigger = row.get_by_role("button", name="Actions for Remy Recordable Bench Basics")
        trigger.scroll_into_view_if_needed()
        expect(trigger).to_be_in_viewport()
        page.wait_for_function(NO_SIDEWAYS_SCROLL)
        assert OrientationRecord.objects.filter(member=target).exists()
