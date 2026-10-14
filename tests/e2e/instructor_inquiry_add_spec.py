"""End-to-end: an admin adds an instructor inquiry by hand on Instructor Inquiries (#794).

Only a browser proves this part: + Add Inquiry opens an Alpine modal, the application's
conditional questions open and close in place inside it (prefixed field names, so the
shared partial must not read a field by name), a failed save reopens the modal on load
with every answer kept and the website field still open, a broken address left under
Website No does not block Save (the form is novalidate), and the save lands the row in
the table as Pending. The Member picker (#805) is a list the page draws: it scrolls inside
the modal, typing narrows it, a click or Enter fills the box, and a member whose stored
name ends in a space saves. ``CAPTURE_794_SCREENSHOTS=1`` also saves the PR's pictures
under ``mockups/screenshots/``: the page, the empty modal and the modal with errors, in
both themes at desktop and phone width; ``CAPTURE_805_SCREENSHOTS=1`` saves the open member
list. Run with ``pytest -m e2e`` on PostgreSQL, under ``--browser chromium`` and
``--browser webkit``.
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
CAPTURE_805 = bool(os.environ.get("CAPTURE_805_SCREENSHOTS"))
WEBSITE_ERROR = "Add your website's address, or pick No."
DESKTOP = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 844}
#: Members enough that the Member list must scroll (#805).
PICKER_NAMES = (
    "Alex Abernathy",
    "Bea Castillo",
    "Cam Duarte",
    "Dee Ellison",
    "Eli Fontaine",
    "Fern Garza",
    "Gus Halvorsen",
    "Hana Ito",
    "Jo Kowalski",
    "Kit Lindqvist",
    "Lou Moreno",
    "Max Nakamura",
    "Nia Okafor",
    "Oz Petrov",
    "Pia Quintero",
    "Rae Sandoval",
    "Sol Tanaka",
    "Tess Underwood",
    "Uma Vargas",
    "Vic Whitaker",
    "Wren Xu",
    "Yara Young",
    "Zoe Zimmer",
    "Ash Bellamy",
)


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
        options = page.locator("[data-member-picker-list] [role=option]")
        expect(options).to_have_count(2)  # Robin and the admin; Avery can already teach
        _capture(page, "modal-empty", size_name)

        # Typing narrows the list; a click on the row fills the box and marks the row.
        member = page.locator("#id_inquiry-member")
        member.press_sequentially("rob")
        robin_row = page.locator('[data-member-option="Robin Reachout"]')
        expect(page.locator("[data-member-picker-list] [role=option]:visible")).to_have_count(1)
        robin_row.click()
        expect(member).to_have_value("Robin Reachout")
        expect(robin_row).to_have_attribute("aria-selected", "true")
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
        expect(page.locator('[data-member-option="Robin Reachout"]')).to_have_attribute("aria-selected", "true")
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

        # A broken address left under No is hidden, not a blocker: the form is novalidate
        # and the server drops it, so Save goes through and stores no website.
        page.locator("#id_inquiry-website").fill("mysite.com")
        _chip(page, "has_website", "No")
        expect(page.locator("#id_inquiry-website")).to_be_hidden()
        page.locator("[data-inquiry-add-form] button[type=submit]").click()

        row = page.locator(f'[data-inquiry-row="{robin.pk}"]')
        expect(row).to_be_visible()
        expect(row).to_contain_text("Pending")
        expect(row).to_contain_text("Silver Rings")
        expect(page.get_by_text("Added an inquiry for Robin Reachout.")).to_be_visible()
        expect(modal).to_be_hidden()
        robin.refresh_from_db()
        assert robin.teaching_application_state == Member.TeachingApplicationState.PENDING
        assert robin.teaching_website == ""
        assert robin.teaching_contact_detail == "503 555 0199"


def describe_the_member_picker():
    def it_scrolls_filters_and_saves_a_member_whose_stored_name_ends_in_a_space(live_server, page, login_via_code):
        page.set_viewport_size(DESKTOP)
        _login_as_admin(login_via_code)
        Member.objects.filter(user__username=ADMIN_EMAIL).update(full_legal_name="Ida Admin", preferred_name="")
        for name in PICKER_NAMES:
            MemberFactory(full_legal_name=name, preferred_name="")
        maria = MemberFactory(full_legal_name="Maria Amelia Randall ", preferred_name="")

        page.goto(f"{live_server.url}{reverse('classes:admin_instructor_inquiries')}")
        page.locator("[data-inquiry-add]").click()
        listbox = page.locator("[data-member-picker-list]")
        expect(listbox).to_be_visible()

        # Every member who may ask is listed, more than fit, so the list scrolls in place.
        expect(listbox.locator("[role=option]")).to_have_count(len(PICKER_NAMES) + 2)  # and Maria and the admin
        assert listbox.evaluate("el => getComputedStyle(el).overflowY") == "auto"
        assert listbox.evaluate("el => el.scrollHeight > el.clientHeight + 40")
        listbox.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        assert listbox.evaluate("el => el.scrollTop > 0")
        if CAPTURE_805:
            SHOTS.mkdir(parents=True, exist_ok=True)
            listbox.evaluate("el => { el.scrollTop = 0; }")
            page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
            page.locator("[data-member-picker]").scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "805-member-picker-light-desktop.png"))
            page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
            page.screenshot(path=str(SHOTS / "805-member-picker-dark-desktop.png"))

        # Nothing matches: the list says so. Enter while filtering never submits the form.
        member = page.locator("#id_inquiry-member")
        page.evaluate("window.plStillHere = true")  # gone if an Enter ever submits the form
        member.press_sequentially("zzz")
        expect(page.locator("[data-member-picker-none]")).to_be_visible()
        member.press("Enter")
        expect(page.locator("[data-inquiry-add-errors]")).to_have_count(0)
        member.fill("")

        # The arrow keys walk the rows still shown, and Enter picks the one walked to.
        member.press_sequentially("maria  amelia")
        maria_row = page.locator('[data-member-option="Maria Amelia Randall"]')
        expect(listbox.locator("[role=option]:visible")).to_have_count(1)
        expect(page.locator("[data-member-picker-none]")).to_be_hidden()
        member.press("ArrowDown")
        expect(member).to_have_attribute("aria-activedescendant", maria_row.get_attribute("id"))
        member.press("Enter")
        expect(member).to_have_value("Maria Amelia Randall")
        expect(maria_row).to_have_attribute("aria-selected", "true")
        assert page.evaluate("window.plStillHere === true")

        page.locator("#id_inquiry-class_title").fill("Enamel Pins")
        page.locator("#id_inquiry-note").fill("Maria emailed about an enamel class.")
        page.locator("[data-inquiry-add-form] button[type=submit]").click()

        row = page.locator(f'[data-inquiry-row="{maria.pk}"]')
        expect(row).to_be_visible()
        expect(row).to_contain_text("Pending")
        maria.refresh_from_db()
        assert maria.teaching_application_state == Member.TeachingApplicationState.PENDING
        assert maria.full_legal_name == "Maria Amelia Randall "
