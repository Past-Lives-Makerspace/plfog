"""End-to-end: an admin re-assigns a discount code from the Discount Codes row menu (#764, part 1).

Only a browser proves this part: the ... menu and the modal are Alpine, the modal body arrives
over htmx, the One class radio reveals the picker, the sentence rewrites as the pick changes and
Re-assign stays disabled until a class is picked. Waits are on what the page shows, never on the
URL (the HX-Redirect lands on the page the admin is already on). ``CAPTURE_764_SCREENSHOTS=1``
also saves the PR's pictures under ``mockups/screenshots/``. Run with ``pytest -m e2e`` on
PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, ClassSessionFactory, DiscountCodeFactory, InstructorFactory
from classes.models import ClassOffering, DiscountCode
from tests.membership.factories import MembershipPlanFactory

ADMIN_EMAIL = "reassign-admin@example.com"
SHOTS = Path("mockups/screenshots")
CAPTURE = bool(os.environ.get("CAPTURE_764_SCREENSHOTS"))
SCOPE_COLUMN = 5  # Code, %, Fixed, Valid, Uses, Scope, Active, Approved, Actions


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


def _seed_bat_class() -> ClassOffering:
    bat = ClassOfferingFactory(title="Make a Halloween Bat", status=ClassOffering.Status.PUBLISHED)
    ClassSessionFactory(class_offering=bat, starts_at=timezone.now() + timedelta(days=7))
    ClassOfferingFactory(title="Gone Already", status=ClassOffering.Status.CANCELLED)
    return bat


def _day(offering: ClassOffering) -> str:
    """The date a Re-assign class option shows for ``offering``'s first session (#818)."""
    return timezone.localtime(offering.sessions.first().starts_at).strftime("%a %b %-d")


def _capture(page, name: str) -> None:
    if CAPTURE:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / name), full_page=False)


def _capture_both_themes(page, slug: str) -> None:
    """The default (dark) shot for the PR, then the light theme to check the same state renders there too."""
    _capture(page, f"{slug}.png")
    if CAPTURE:
        page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
        _capture(page, f"{slug}-light.png")
        page.evaluate("document.documentElement.removeAttribute('data-theme')")


def _open_reassign(page, live_server, code: str):
    page.goto(f"{live_server.url}{reverse('classes:admin_discount_codes')}")
    row = page.locator("tbody tr", has_text=code)
    row.get_by_role("button", name=f"Actions for {code}").click()
    menu = row.get_by_role("menu")
    expect(menu).to_be_visible()
    return row, menu


def describe_an_admin():
    def it_re_assigns_a_code_to_one_class_from_the_row_menu(live_server, page, login_via_code):
        bat = _seed_bat_class()
        code = DiscountCodeFactory(code="JACQUELINE30", class_offering=None, is_approved=True, discount_pct=30)
        _login_as_admin(login_via_code)
        page.set_viewport_size({"width": 1200, "height": 800})

        row, menu = _open_reassign(page, live_server, "JACQUELINE30")
        expect(row.locator("td").nth(SCOPE_COLUMN)).to_have_text("Everywhere")
        for item in ("Unapprove", "Edit", "Re-assign", "Delete"):
            expect(menu.get_by_role("menuitem", name=item)).to_be_visible()
        expect(menu).to_have_css("opacity", "1")  # the fade in has finished, so the shot is not half drawn
        _capture_both_themes(page, "764-actions-menu-01")

        menu.get_by_role("menuitem", name="Re-assign").click()
        dialog = page.get_by_role("dialog", name="Re-assign Discount Code")
        form = dialog.locator("[data-reassign-form]")
        sentence = form.locator("[data-reassign-sentence]")
        expect(form.get_by_role("radio", name="Everywhere")).to_be_checked()
        expect(sentence).to_have_text("JACQUELINE30 will work on every class.")
        picker = form.get_by_label("Class", exact=True)
        expect(picker).to_be_hidden()

        form.get_by_role("radio", name="One class").check()
        expect(picker).to_be_visible()
        submit = form.get_by_role("button", name="Re-assign")
        expect(submit).to_be_disabled()
        expect(sentence).to_have_text("Pick a class to see what will change.")
        # Upcoming classes only: the cancelled one never shows.
        # Two runs of one title read apart by their dates and status (#818).
        rerun = ClassOfferingFactory(title="Make a Halloween Bat", slug="make-a-halloween-bat-draft")
        ClassSessionFactory(class_offering=rerun, starts_at=timezone.now() + timedelta(days=21))
        page.reload()
        _, menu = _open_reassign(page, live_server, "JACQUELINE30")
        menu.get_by_role("menuitem", name="Re-assign").click()
        dialog = page.get_by_role("dialog", name="Re-assign Discount Code")
        form = dialog.locator("[data-reassign-form]")
        sentence = form.locator("[data-reassign-sentence]")
        picker = form.get_by_label("Class", exact=True)
        submit = form.get_by_role("button", name="Re-assign")
        form.get_by_role("radio", name="One class").check()
        bat_label = f"Make a Halloween Bat · {_day(bat)}"
        expect(picker.locator("option")).to_have_text(
            ["Pick a class", bat_label, f"Make a Halloween Bat · {_day(rerun)} · Draft"]
        )
        picker.evaluate("el => { el.size = 3; }")
        page.screenshot(path=str(SHOTS / "818-reassign-class-picker.png"), full_page=False)
        picker.evaluate("el => { el.size = 0; }")
        picker.select_option(label=bat_label)
        expect(sentence).to_have_text("JACQUELINE30 will work only on Make a Halloween Bat.")
        expect(submit).to_be_enabled()
        _capture_both_themes(page, "764-reassign-modal-02")

        submit.click()
        # The message is drained into a toast (hub/base.html keeps the hidden source list), so read the toast.
        expect(
            page.locator("#plt-container").get_by_text("JACQUELINE30 now works only on Make a Halloween Bat.")
        ).to_be_visible()
        expect(page.locator("tbody tr", has_text="JACQUELINE30").locator("td").nth(SCOPE_COLUMN)).to_have_text(
            "Make a Halloween Bat"
        )
        code.refresh_from_db()
        assert code.class_offering == bat
        assert code.is_approved is True

    def it_sets_a_class_code_back_to_everywhere_and_keeps_its_approval(live_server, page, login_via_code):
        bat = _seed_bat_class()
        code = DiscountCodeFactory(code="BAT30", class_offering=bat, is_approved=True, discount_pct=30)
        _login_as_admin(login_via_code)
        page.set_viewport_size({"width": 1200, "height": 800})

        row, menu = _open_reassign(page, live_server, "BAT30")
        expect(row.locator("td").nth(SCOPE_COLUMN)).to_have_text("Make a Halloween Bat")
        menu.get_by_role("menuitem", name="Re-assign").click()
        dialog = page.get_by_role("dialog", name="Re-assign Discount Code")
        form = dialog.locator("[data-reassign-form]")
        # Preselected to the current scope: One class, with its class already picked.
        expect(form.get_by_role("radio", name="One class")).to_be_checked()
        expect(form.get_by_label("Class", exact=True)).to_have_value(str(bat.pk))
        expect(form.locator("[data-reassign-sentence]")).to_have_text("BAT30 will work only on Make a Halloween Bat.")

        form.get_by_role("radio", name="Everywhere").check()
        expect(form.get_by_label("Class", exact=True)).to_be_hidden()
        expect(form.locator("[data-reassign-sentence]")).to_have_text("BAT30 will work on every class.")
        form.get_by_role("button", name="Re-assign").click()
        expect(page.locator("#plt-container").get_by_text("BAT30 now works on every class.")).to_be_visible()
        expect(page.locator("tbody tr", has_text="BAT30").locator("td").nth(SCOPE_COLUMN)).to_have_text("Everywhere")
        code.refresh_from_db()
        assert code.class_offering is None
        assert code.is_approved is True

    def it_re_assigns_a_code_to_an_instructors_classes_and_the_class_tab_shows_it(live_server, page, login_via_code):
        """Part 2 of #764: the third answer, and AC6 on the class's Discount Codes tab."""
        teacher = InstructorFactory(full_legal_name="Jacqueline Sowell")
        bat = ClassOfferingFactory(
            title="Make a Halloween Bat", instructor=teacher, status=ClassOffering.Status.PUBLISHED
        )
        ClassSessionFactory(class_offering=bat, starts_at=timezone.now() + timedelta(days=7))
        ClassOfferingFactory(title="Someone Else's Class", status=ClassOffering.Status.PUBLISHED)
        code = DiscountCodeFactory(code="JACQUELINE30", class_offering=None, is_approved=True, discount_pct=30)
        _login_as_admin(login_via_code)
        page.set_viewport_size({"width": 1200, "height": 800})

        _, menu = _open_reassign(page, live_server, "JACQUELINE30")
        menu.get_by_role("menuitem", name="Re-assign").click()
        dialog = page.get_by_role("dialog", name="Re-assign Discount Code")
        form = dialog.locator("[data-reassign-form]")
        sentence = form.locator("[data-reassign-sentence]")
        picker = form.get_by_label("Instructor", exact=True)
        expect(picker).to_be_hidden()
        form.get_by_role("radio", name="An instructor's classes").check()
        expect(picker).to_be_visible()
        expect(form.get_by_label("Class", exact=True)).to_be_hidden()
        submit = form.get_by_role("button", name="Re-assign")
        expect(submit).to_be_disabled()
        expect(sentence).to_have_text("Pick an instructor to see what will change.")
        picker.select_option(label="Jacqueline Sowell")
        expect(sentence).to_have_text("JACQUELINE30 will work on every class Jacqueline Sowell teaches, now and later.")
        expect(submit).to_be_enabled()
        _capture_both_themes(page, "764-reassign-instructor-03")

        submit.click()
        expect(
            page.locator("#plt-container").get_by_text(
                "JACQUELINE30 now works on every class Jacqueline Sowell teaches, now and later."
            )
        ).to_be_visible()
        expect(page.locator("tbody tr", has_text="JACQUELINE30").locator("td").nth(SCOPE_COLUMN)).to_have_text(
            "Jacqueline Sowell"
        )
        code.refresh_from_db()
        assert (code.instructor, code.class_offering, code.is_approved) == (teacher, None, True)

        # AC6: the class's own Discount Codes tab lists the code with its scope.
        page.goto(f"{live_server.url}{reverse('classes:teach_class_discount_codes', kwargs={'pk': bat.pk})}")
        row = page.locator("tbody tr", has_text="JACQUELINE30")
        expect(row.locator("[data-scope-instructor]")).to_have_text("Jacqueline Sowell")
        _capture_both_themes(page, "764-class-tab-instructor-04")

    def it_closes_on_cancel_without_moving_the_code(live_server, page, login_via_code):
        _seed_bat_class()
        DiscountCodeFactory(code="PLAIN10", class_offering=None)
        _login_as_admin(login_via_code)
        _, menu = _open_reassign(page, live_server, "PLAIN10")
        menu.get_by_role("menuitem", name="Re-assign").click()
        form = page.get_by_role("dialog", name="Re-assign Discount Code").locator("[data-reassign-form]")
        form.get_by_role("radio", name="One class").check()
        form.get_by_role("button", name="Cancel").click()
        expect(page.get_by_role("dialog", name="Re-assign Discount Code")).to_be_hidden()
        assert DiscountCode.objects.get(code="PLAIN10").class_offering is None
