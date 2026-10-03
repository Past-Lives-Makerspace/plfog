"""End-to-end: a guild editor adds a weekly Studio Hours window, then deletes it.

The Studio Hours tab's editor is its OWN ``<form>`` (outside the main guild form) and, since
#575, saves itself on every change: there is no Save button. Unit tests cover the formset
logic; only a real browser proves the whole structure works: the tab reveals, the "+ Add"
clone-empty_form JS builds a live row, each pick posts the form (a row with a time still
blank is skipped, not refused; once both are picked the row lands and its hidden id is
stamped), and
the row's Delete asks once and then takes the window off the page and the calendar. Run
with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import CommunityEvent
from tests.membership.factories import GuildFactory, MembershipPlanFactory

ADMIN_EMAIL = "studio-hours-admin@example.com"
ALPINE_READY = "() => !!(document.querySelector('[data-guild-autosave]') || {})._x_dataStack"
SAVED_PAST = (
    "(n) => { const pill = document.querySelector('[data-save-pill]');"
    " return !!pill && Number(pill.dataset.saves) >= n && pill.textContent.trim() === 'Saved'; }"
)


def _saves(page) -> int:
    return int(page.locator("[data-save-pill]").get_attribute("data-saves") or 0)


def describe_guild_studio_hours_editor():
    def it_adds_a_weekly_window_then_deletes_it(live_server, page, login_via_code):
        # A plan must exist so the login signal auto-creates the member.
        MembershipPlanFactory()
        guild = GuildFactory(name="Ceramics Guild")

        # Sign in through the real code flow, then elevate to admin so the
        # guild-editor gate passes (compute_actual_roles grants admin from is_superuser).
        login_via_code(ADMIN_EMAIL)
        user = get_user_model().objects.get(username=ADMIN_EMAIL)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        # Open the guild editor straight onto the Studio Hours tab (?tab= seeds Alpine's section).
        page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}?tab=studio_hours")
        page.wait_for_function(ALPINE_READY)
        expect(page.locator("[data-formset-empty]", has_text="No studio hours yet")).to_be_visible()

        # Add a row (clones #studio-hours-empty-template, bumps TOTAL_FORMS to index 0). Every
        # pick posts at once, but with both times still blank the row is half typed: it posts
        # as rendered and is skipped, with no error and nothing landed.
        page.get_by_role("button", name="+ Add studio hours", exact=True).click()
        page.select_option('select[name="studio_hours-0-weekday"]', "1")  # Tuesday
        page.wait_for_function(SAVED_PAST, arg=1)
        expect(page.locator("#studio-hours-rows .pl-field-error")).to_have_count(0)
        assert not guild.events.studio_hours().exists()
        assert page.locator('input[name="studio_hours-0-id"]').input_value() == ""
        page.select_option('select[name="studio_hours-0-start_time"]', "14:00")
        page.select_option('select[name="studio_hours-0-end_time"]', "17:00")
        # The row's hidden pk distinguishes a landed row from the pre-save DOM, whose visible
        # values are identical.
        expect(page.locator('input[name="studio_hours-0-id"]')).not_to_have_value("")
        expect(page.locator("#studio-hours-rows .pl-field-error")).to_have_count(0)

        before = _saves(page)
        location = page.locator('input[name="studio_hours-0-location"]')
        location.fill("Kiln room")
        location.press("Tab")
        page.wait_for_function(SAVED_PAST, arg=before + 1)
        expect(page.locator('select[name="studio_hours-0-weekday"]')).to_have_value("1")
        expect(page.locator('select[name="studio_hours-0-start_time"]')).to_have_value("14:00")

        # And it really landed as a weekly, published STUDIO_HOURS event on the guild.
        event = guild.events.studio_hours().get()
        assert event.recurrence == CommunityEvent.Recurrence.WEEKLY
        assert event.title == "Ceramics Guild Studio Hours"
        assert event.location == "Kiln room"

        # A reload shows the same row, served with its id.
        page.reload()
        page.wait_for_function(ALPINE_READY)
        expect(page.locator('input[name="studio_hours-0-id"]')).to_have_value(str(event.pk))
        expect(page.locator('input[name="studio_hours-0-location"]')).to_have_value("Kiln room")

        # Delete asks once; confirming flips the hidden DELETE flag and the form saves itself.
        page.locator("#studio-hours-rows [data-formset-row]").get_by_role("button", name="Delete", exact=True).click()
        expect(page.get_by_role("dialog")).to_contain_text("Delete these hours?")
        page.get_by_role("dialog").get_by_role("button", name="Delete", exact=True).click()
        expect(page.locator("[data-formset-empty]", has_text="No studio hours yet")).to_be_visible()
        expect(page.locator("#studio-hours-rows [data-formset-row]")).to_have_count(0)
        assert not guild.events.studio_hours().exists()
