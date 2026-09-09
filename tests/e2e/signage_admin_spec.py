"""End-to-end: an admin adds a Slideshow slide from /manage/slideshow/, in a real browser.

The page stacks THREE sibling ``<form>``s (screens, slides, then the automatic-slide
settings — you can't nest forms). Our unit tests guard that structure by parsing the
HTML; only a real browser proves the whole thing works: the "+ Add" clone-empty_form JS
builds a live row and that card's own Save button persists it. Run with ``pytest -m e2e``.
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

        # Open the Slideshow admin page.
        page.goto(f"{live_server.url}{reverse('hub_admin_slideshow')}")

        # Add a slide row (clones #slide-empty-template, bumps TOTAL_FORMS to index 0).
        page.get_by_role("button", name="+ Add a slide", exact=True).click()

        # Fill the new custom slide. Title/body live inside the x-show="custom" block, which
        # Alpine reveals once the cloned row initializes; Playwright waits for that.
        page.fill('input[name="slides-0-title"]', "Wall welcome")
        page.fill('textarea[name="slides-0-body"]', "Ask the front desk for a tour.")

        # Save the slides editor form (its own form, not the settings form). Every Save on
        # this page now reads just "Save" (Rule 21), so scope the lookup to the slides card
        # or Playwright's strict mode matches three buttons.
        slides_form = page.locator(f'form[action="{reverse("hub_admin_slideshow_slides_save")}"]')
        slides_form.get_by_role("button", name="Save", exact=True).click()

        # The save redirects back to the Slideshow page; the persisted row now renders with
        # our title in its editable field.
        expect(page).to_have_url(re.compile(re.escape(reverse("hub_admin_slideshow")) + r"$"))
        expect(page.locator('input[name="slides-0-title"]')).to_have_value("Wall welcome")

        # And it really landed in the database.
        from membership.models import SlideshowSlide

        assert SlideshowSlide.objects.filter(title="Wall welcome").exists()
