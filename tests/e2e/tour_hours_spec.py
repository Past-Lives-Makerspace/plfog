"""End-to-end: Tour Settings save themselves, and say Not saved on the card holding the error (#1279).

A guide's hours, dates and days off are set on My Hours since #1300 (``tests/e2e/my_hours_editing_spec.py``,
``tests/e2e/my_hours_doors_spec.py``); the Tours page's own My Hours tab went with it. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os

from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory
from tours.models import FacilityTourSettings

GUIDE_EMAIL = "tour-guide-hours@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_947_DIR")
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}


def _make_guide() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=GUIDE_EMAIL, email=GUIDE_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Gina Guide"
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def describe_tour_settings_autosave():
    def it_says_not_saved_on_the_card_that_holds_the_error(live_server, page, login_via_code):
        admin = _make_guide()
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:settings')}")
        page.wait_for_function("() => !!document.querySelector('[data-guild-autosave]').plGuildAutosave")
        assert page.locator("[data-tour-noscript-save]").is_hidden()

        # Bookings off with no closed message: refused, Not saved on Availability.
        page.locator('input[name="closed_message"]').evaluate("(el) => { el.value = ''; }")
        page.locator('[data-tour-settings-availability] input[name="accepting_bookings"]').evaluate(
            "(el) => el.click()"
        )
        availability = page.locator('[data-tour-save-status="availability"]')
        availability.get_by_text("Not saved").wait_for()
        page.locator("[data-tour-settings-availability] [data-autosave-error]").wait_for()

        # An edit on Booking posts the same form: still refused, and the pill stays where the reason is.
        with page.expect_response(lambda r: r.request.method == "POST" and r.status == 422):
            page.locator('input[name="booking_horizon_days"]').fill("45")
            page.locator('input[name="booking_horizon_days"]').dispatch_event("change")
        page.wait_for_function("() => document.querySelector('[data-guild-autosave]').plGuildAutosave.pending === 0")
        availability.get_by_text("Not saved").wait_for()
        assert page.locator('[data-tour-save-status="booking"]').is_hidden()
        assert FacilityTourSettings.load().booking_horizon_days == 30

        # Writing the message saves everything.
        page.locator('input[name="closed_message"]').fill("Back in December.")
        availability.get_by_text("Saved", exact=True).wait_for()
        saved = FacilityTourSettings.load()
        assert (saved.accepting_bookings, saved.booking_horizon_days) == (False, 45)


#: ``CAPTURE_1160_DIR=<dir>`` saves the What's New shots (``step-1.png`` to ``step-3.png``, the
#: standard frame) and the PR's light theme and phone pictures of Available On (#1160).
CAPTURE_1160_DIR = os.environ.get("CAPTURE_1160_DIR")
