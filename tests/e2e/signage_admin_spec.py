"""End-to-end: an admin adds a Slideshow slide from Site Settings, in a real browser.

The Slideshow tab holds TWO stacked ``<form>``s (the settings form, then the zones
and slides editors as separate forms after it — you can't nest forms). Our unit tests
guard that structure by parsing the HTML; only a real browser proves the whole thing
works: the tab reveals, the "+ Add" clone-empty_form JS builds a live row, and the
row's own Save button persists it. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import MembershipPlanFactory

ADMIN_EMAIL = "slideshow-admin@example.com"


def describe_signage_admin_editor():
    def it_adds_a_slide_and_persists_it(live_server, page, login_via_code):
        # A plan must exist so the login signal auto-creates the member.
        MembershipPlanFactory()

        # Sign in through the real code flow, then elevate to admin so @fog_admin_required
        # passes (compute_actual_roles grants admin from is_superuser).
        login_via_code(ADMIN_EMAIL)
        user = get_user_model().objects.get(username=ADMIN_EMAIL)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        # Open Site Settings and switch to the Slideshow tab (Alpine reveals Block B).
        page.goto(f"{live_server.url}{reverse('hub_admin_site_settings')}")
        page.get_by_role("button", name="Slideshow", exact=True).click()

        # Add a slide row (clones #slide-empty-template, bumps TOTAL_FORMS to index 0).
        page.get_by_role("button", name="+ Add a slide", exact=True).click()

        # Fill the new custom slide. Title/body live inside the x-show="custom" block, which
        # Alpine reveals once the cloned row initializes; Playwright waits for that.
        page.fill('input[name="slides-0-title"]', "Wall welcome")
        page.fill('textarea[name="slides-0-body"]', "Ask the front desk for a tour.")
        page.fill('input[name="slides-0-duration_seconds"]', "8")

        # Save the slides editor form (its own form, not the settings form).
        page.get_by_role("button", name="Save slides", exact=True).click()

        # The save redirects back to the Slideshow tab; the persisted row now renders with
        # our title in its editable field.
        expect(page).to_have_url(re.compile(r"tab=slideshow"))
        expect(page.locator('input[name="slides-0-title"]')).to_have_value("Wall welcome")

        # And it really landed in the database.
        from membership.models import SlideshowSlide

        assert SlideshowSlide.objects.filter(title="Wall welcome").exists()
