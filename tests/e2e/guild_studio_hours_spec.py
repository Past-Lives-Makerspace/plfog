"""End-to-end: a guild editor adds a weekly Studio Hours window, then deletes it.

The Studio Hours tab's editor is its OWN ``<form>`` (outside the main guild
form — you can't nest forms, and nesting silently breaks Save). Unit tests cover the
formset logic; only a real browser proves the whole structure works: the tab reveals,
the "+ Add" clone-empty_form JS builds a live row, the row's Save persists it, and the
per-row Delete button auto-submits the removal. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import CommunityEvent
from tests.membership.factories import GuildFactory, MembershipPlanFactory

ADMIN_EMAIL = "studio-hours-admin@example.com"


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
        expect(page.locator("body")).to_contain_text("No studio hours yet")

        # Add a row (clones #studio-hours-empty-template, bumps TOTAL_FORMS to index 0).
        page.get_by_role("button", name="+ Add studio hours", exact=True).click()
        page.select_option('select[name="studio_hours-0-weekday"]', "1")  # Tuesday
        page.select_option('select[name="studio_hours-0-start_time"]', "14:00")
        page.select_option('select[name="studio_hours-0-end_time"]', "17:00")
        page.fill('input[name="studio_hours-0-location"]', "Kiln room")

        # Save the studio-hours form (its own form, not the main guild form).
        page.get_by_role("button", name="Save Studio Hours", exact=True).click()

        # Wait for the POST-redirect page via the row's hidden pk input — the URL and
        # the visible field values are identical before and after the round-trip, so
        # only the assigned pk distinguishes the redirected page from the pre-submit
        # DOM (asserting the DB before the redirect lands races the in-flight request).
        expect(page.locator('input[name="studio_hours-0-id"]')).not_to_have_value("")
        expect(page).to_have_url(re.compile(r"tab=studio_hours"))
        expect(page.locator('select[name="studio_hours-0-weekday"]')).to_have_value("1")
        expect(page.locator('select[name="studio_hours-0-start_time"]')).to_have_value("14:00")
        expect(page.locator('input[name="studio_hours-0-location"]')).to_have_value("Kiln room")

        # And it really landed as a weekly, published STUDIO_HOURS event on the guild.
        event = guild.events.studio_hours().get()
        assert event.recurrence == CommunityEvent.Recurrence.WEEKLY
        assert event.title == "Ceramics Guild Studio Hours"
        assert event.location == "Kiln room"

        # The persisted row's Delete button flips the hidden DELETE flag and auto-saves.
        page.get_by_role("button", name="Delete", exact=True).click()
        expect(page.locator("body")).to_contain_text("No studio hours yet")
        assert not guild.events.studio_hours().exists()
