"""End-to-end (#1072): an admin makes a Members only code, and checkout refuses it to a signed out buyer.

The admin switches Members only on in the New Discount Code form and the list tags the code.
A signed out buyer who types MEMBER10 on a class's register page is told "This code is for
members. Sign in to use it." under the code box and is quoted full price. With
``PL_SCREENSHOT_DIR`` set, the two steps are saved there at the guidance frame
(``tests/e2e/guidance.py``) as ``step-1.png`` and ``step-2.png``. Run with ``pytest -m e2e``.
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

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, DiscountCodeFactory, daytime
from classes.models import ClassOffering, DiscountCode
from tests.e2e.guidance import FRAMES, shoot
from tests.membership.factories import MembershipPlanFactory

ADMIN_EMAIL = "members-only-admin@example.com"
REFUSAL = "This code is for members. Sign in to use it."


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates the tables (discount_code_requests_spec has the why)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _shoot(page, step: int, locator, target: str | None) -> None:
    """Save one guidance step, only when ``PL_SCREENSHOT_DIR`` names a folder."""
    folder = os.environ.get("PL_SCREENSHOT_DIR")
    if not folder:
        return
    page.set_viewport_size(FRAMES["desktop"])
    locator.scroll_into_view_if_needed()
    locator.evaluate("(el) => el.scrollIntoView({block: 'center'})")
    shoot(page, Path(folder) / f"step-{step}.png", target=target)


def describe_an_admin():
    def it_makes_a_members_only_code_and_the_list_tags_it(live_server, page, login_via_code):
        MembershipPlanFactory()  # the admin's login signal needs a plan to provision their member
        login_via_code(ADMIN_EMAIL)
        user = get_user_model().objects.get(username=ADMIN_EMAIL)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        page.goto(f"{live_server.url}{reverse('classes:admin_discount_code_create')}")
        page.fill('input[name="code"]', "GUILDMEMBERS")
        page.fill('input[name="discount_pct"]', "15")
        switch = page.locator('[data-guidance-target="classes.members-only"]')
        expect(switch).to_contain_text("Only signed in members with an active membership can use it.")
        switch.locator("label.pl-toggle").click()
        expect(page.locator('input[name="members_only"]')).to_be_checked()
        _shoot(page, 1, switch, "classes.members-only")

        page.get_by_role("button", name="Save").click()
        expect(page).to_have_url(f"{live_server.url}{reverse('classes:admin_discount_codes')}")
        assert DiscountCode.objects.get(code="GUILDMEMBERS").members_only is True
        row = page.locator("tr", has_text="GUILDMEMBERS")
        expect(row.locator("[data-members-only]")).to_have_text("Members only")


def describe_a_signed_out_buyer():
    def it_is_refused_the_code_and_quoted_full_price(live_server, page):
        offering = ClassOfferingFactory(
            title="Pewter Casting Basics",
            slug="pewter-casting-basics",
            category=CategoryFactory(name="Metal Casting"),
            status=ClassOffering.Status.PUBLISHED,
            is_private=False,
            price_cents=8500,
        )
        starts = daytime(timezone.now() + timedelta(days=14))
        ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=3))
        DiscountCodeFactory(code="MEMBER10", discount_pct=10, class_offering=None, members_only=True)

        page.goto(f"{live_server.url}{reverse('classes:register', kwargs={'slug': offering.slug})}")
        page.fill('input[name="first_name"]', "Casey")
        page.fill('input[name="last_name"]', "Visitor")
        page.fill('input[name="email"]', "casey.visitor@example.com")
        page.fill('input[name="liability_signature"]', "Casey Visitor")
        page.check('input[name="accepts_liability"]')
        code_box = page.locator('#reg-form input[name="discount_code"]')
        code_box.fill("MEMBER10")
        code_box.press("Tab")
        expect(page.locator("#reg-submit-label")).to_have_text("$85")
        page.locator("button.btn-reg").click()

        refusal = page.locator(".reg-errors", has_text=REFUSAL)
        expect(refusal).to_be_visible()
        expect(page.locator('#reg-form input[name="discount_code"]')).to_have_value("MEMBER10")
        expect(page.locator("#reg-submit-label")).to_have_text("$85")
        _shoot(page, 2, refusal, None)
