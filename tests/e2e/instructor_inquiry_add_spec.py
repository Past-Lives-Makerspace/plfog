"""End-to-end: an admin adds an instructor inquiry by hand on Instructor Inquiries (#794).

Only a browser proves this part: + Add Inquiry opens an Alpine modal, the application's
conditional questions open and close in place inside it (prefixed field names, so the
shared partial must not read a field by name), a failed save reopens the modal on load
with every answer kept and the website field still open, and a good save lands the row
in the table as Pending. ``CAPTURE_794_SCREENSHOTS=1`` also saves the PR's pictures
under ``mockups/screenshots/``: the page, the empty modal and the modal with errors, in
both themes at desktop and phone width. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

ADMIN_EMAIL = "inquiry-admin@example.com"
SHOTS = Path("mockups/screenshots")
CAPTURE = bool(os.environ.get("CAPTURE_794_SCREENSHOTS"))
WEBSITE_ERROR = "Add your website's address, or pick No."
DESKTOP = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 844}


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates the tables (discount_code_requests_spec has the why)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _login_as_admin(login_via_code) -> None:
    MembershipPlanFactory()  # the login signal needs a plan to provision the admin's member
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])


def _chip(page: Page, field: str, label: str) -> None:
    """Click the chip for ``label`` in the prefixed ``field``'s group."""
    page.locator(f'label.pl-chip:has(input[name="inquiry-{field}"])', has_text=label).click()


def _capture(page: Page, slug: str, size_name: str) -> None:
    """Dark and light of whatever the page shows now, at the run's width."""
    if not CAPTURE:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        page.evaluate(f"document.documentElement.setAttribute('data-theme', '{theme}')")
        page.screenshot(path=str(SHOTS / f"794-{slug}-{theme}-{size_name}.png"))
    page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")


def describe_add_inquiry():
    # The whole walk at both widths: the sidebar settles open or closed at load, so a
    # phone run starts at phone width rather than resizing a desktop page.
    @pytest.mark.parametrize("size_name", ["desktop", "phone"])
    def it_adds_an_inquiry_from_the_modal_and_lists_it_as_pending(live_server, page, login_via_code, size_name):
        page.set_viewport_size(DESKTOP if size_name == "desktop" else PHONE)
        _login_as_admin(login_via_code)
        robin = MemberFactory(full_legal_name="Robin Reachout", preferred_name="")
        MemberFactory(
            full_legal_name="Avery Approved",
            preferred_name="",
            teaching_applied_at=timezone.now(),
            instructor_oriented_at=timezone.now(),
            teaching_application_note="Avery wants to teach casting.",
            teaching_class_title="Lost Wax Casting",
        )

        page.goto(f"{live_server.url}{reverse('classes:admin_instructor_inquiries')}")
        add = page.locator("[data-inquiry-add]")
        expect(add).to_be_visible()
        _capture(page, "page", size_name)

        modal = page.locator("[data-inquiry-add-form]")
        expect(modal).to_be_hidden()
        add.click()
        expect(modal).to_be_visible()
        expect(page.locator("#id_inquiry-reached_out_on")).to_have_value(timezone.localdate().isoformat())
        options = page.locator("#inquiry-member-options option")
        expect(options).to_have_count(2)  # Robin and the admin; Avery can already teach
        _capture(page, "modal-empty", size_name)

        page.locator("#id_inquiry-member").fill("Robin Reachout")
        page.locator("#id_inquiry-class_title").fill("Silver Rings")
        page.locator("#id_inquiry-note").fill("Robin asked at the front desk about a ring class.")

        # The conditional questions open and close in place, as on the application page.
        taught_where = page.locator("#id_inquiry-taught_where")
        expect(taught_where).to_be_hidden()
        _chip(page, "experience", "Taught a few classes")
        expect(taught_where).to_be_visible()
        _chip(page, "experience", "First time teaching")
        expect(taught_where).to_be_hidden()
        _chip(page, "experience", "Taught a few classes")

        website = page.locator("#id_inquiry-website")
        expect(website).to_be_hidden()
        _chip(page, "has_website", "Yes")
        expect(website).to_be_visible()

        # The detail label follows the method, read from the prefixed select.
        detail_label = page.locator('label[for="id_inquiry-contact_detail"]')
        expect(detail_label).to_have_text("Where to reach you")
        page.select_option("#id_inquiry-contact_method", "text")
        expect(detail_label).to_have_text("Phone number for texts")
        page.locator("#id_inquiry-contact_detail").fill("503 555 0199")

        # Website yes with no address: the page comes back with the modal open, the error
        # beside the field, the field still open and every answer kept.
        page.locator("[data-inquiry-add-form] button[type=submit]").click()
        expect(page.get_by_text(WEBSITE_ERROR)).to_be_visible()
        expect(modal).to_be_visible()
        expect(page.locator("#id_inquiry-website")).to_be_visible()
        expect(page.locator("#id_inquiry-member")).to_have_value("Robin Reachout")
        expect(page.locator("#id_inquiry-class_title")).to_have_value("Silver Rings")
        expect(page.locator('input[name="inquiry-experience"][value="a_few"]')).to_be_checked()
        expect(page.locator("#id_inquiry-taught_where")).to_be_visible()
        expect(detail_label).to_have_text("Phone number for texts")
        expect(page.locator("#id_inquiry-contact_detail")).to_have_value("503 555 0199")
        robin.refresh_from_db()
        assert robin.teaching_applied_at is None
        _capture(page, "modal-errors", size_name)
        page.locator("#id_inquiry-website").scroll_into_view_if_needed()
        _capture(page, "modal-error-field", size_name)
        page.locator("[data-inquiry-add-form] button[type=submit]").scroll_into_view_if_needed()
        _capture(page, "modal-save", size_name)

        page.locator("#id_inquiry-website").fill("https://robin.example")
        page.locator("[data-inquiry-add-form] button[type=submit]").click()

        row = page.locator(f'[data-inquiry-row="{robin.pk}"]')
        expect(row).to_be_visible()
        expect(row).to_contain_text("Pending")
        expect(row).to_contain_text("Silver Rings")
        expect(page.get_by_text("Added an inquiry for Robin Reachout.")).to_be_visible()
        expect(modal).to_be_hidden()
        robin.refresh_from_db()
        assert robin.teaching_application_state == Member.TeachingApplicationState.PENDING
        assert robin.teaching_website == "https://robin.example"
        assert robin.teaching_contact_detail == "503 555 0199"
