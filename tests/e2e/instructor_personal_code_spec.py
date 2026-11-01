"""End-to-end (#1065): an instructor sees their personal code and the codes on their classes with codes switched off.

With ``instructor_discount_codes_enabled`` off (production's setting), an instructor still finds
their personal code on My Classes with a Copy button, the Discount Codes tab with every live code
that works on their classes, and the codes on one class on its Overview; none of those pages
offers making or asking for a code. With ``PL_SCREENSHOT_DIR`` set, the three steps are saved
there at the guidance frame (``tests/e2e/guidance.py``) as ``step-1.png`` to ``step-3.png``.
Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, DiscountCodeFactory, daytime
from classes.models import ClassOffering, DiscountCode
from core.models import SiteConfiguration
from membership.models import Member
from tests.e2e.guidance import FRAMES, shoot
from tests.membership.factories import MembershipPlanFactory

TEACHER_EMAIL = "personal-code-teacher@example.com"


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates the tables (discount_code_requests_spec has the why)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _shoot(page, step: int, target: str) -> None:
    """Save one guidance step, only when ``PL_SCREENSHOT_DIR`` names a folder."""
    folder = os.environ.get("PL_SCREENSHOT_DIR")
    if not folder:
        return
    page.set_viewport_size(FRAMES["desktop"])
    page.locator(f'[data-guidance-target="{target}"]').scroll_into_view_if_needed()
    shoot(page, Path(folder) / f"step-{step}.png", target=target)


def _seed(login_via_code) -> ClassOffering:
    """Sign the instructor in, grant teaching the way an admin does, and give them a class and some codes."""
    config = SiteConfiguration.load()
    config.instructor_discount_codes_enabled = False
    config.save(update_fields=["instructor_discount_codes_enabled"])
    MembershipPlanFactory()
    login_via_code(TEACHER_EMAIL)
    member = Member.objects.get(user__username=TEACHER_EMAIL)
    Member.objects.filter(pk=member.pk).update(full_legal_name="Robin Example", preferred_name="")
    member.refresh_from_db()
    member.grant_teaching(granted_by=None)
    offering = ClassOfferingFactory(
        title="Pewter Casting Basics",
        instructor=member,
        category=CategoryFactory(name="Metal Casting"),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=8500,
    )
    starts = daytime(timezone.now() + timezone.timedelta(days=14))
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timezone.timedelta(hours=3))
    DiscountCodeFactory(code="WELCOME15", discount_pct=15, class_offering=None, description="Seeded site wide code")
    DiscountCodeFactory(code="PEWTER20", discount_pct=20, class_offering=offering)
    DiscountCodeFactory(code="NOTYET", class_offering=offering, is_approved=False)
    return offering


def describe_an_instructor_with_codes_switched_off():
    def it_sees_the_card_the_tab_and_the_codes_on_a_class(live_server, page, login_via_code):
        offering = _seed(login_via_code)
        assert DiscountCode.objects.get(is_personal=True).code == "ROBIN10"

        # Step 1: My Classes leads with the personal code and its Copy button.
        page.goto(f"{live_server.url}{reverse('classes:teach_dashboard')}")
        card = page.locator("[data-personal-code]")
        expect(card).to_be_visible()
        expect(card.locator("[data-personal-code-value]")).to_have_value("ROBIN10")
        expect(card).to_contain_text("Works on every class you teach. Share it with anyone.")
        copy = card.get_by_role("button", name="Copy", exact=True)
        _shoot(page, 1, "classes.personal-code")
        copy.click()
        expect(card.locator("[data-personal-code-copy]")).to_have_text("Copied!")

        # Step 2: the Discount Codes tab is offered, with the card and the live codes, read only.
        page.locator(".pl-tabs").get_by_role("link", name="Discount Codes", exact=True).click()
        expect(page).to_have_url(f"{live_server.url}{reverse('classes:teach_discount_codes')}")
        expect(page.locator("[data-personal-code-value]")).to_have_value("ROBIN10")
        working = page.locator("[data-working-codes]")
        expect(working.locator("code", has_text="WELCOME15")).to_be_visible()
        expect(working.locator("code", has_text="PEWTER20")).to_be_visible()
        expect(page.locator("code", has_text="NOTYET")).to_have_count(0)
        expect(page.locator("[data-ask-an-admin]")).to_be_visible()
        for name in ("+ New Code", "Request a Code"):
            expect(page.get_by_role("link", name=name, exact=True)).to_have_count(0)
        _shoot(page, 2, "classes.working-codes")

        # Step 3: the class's Overview lists the codes that work on it, with the way back to the tab.
        page.goto(f"{live_server.url}{reverse('classes:teach_class_detail', kwargs={'pk': offering.pk})}")
        section = page.locator("[data-class-codes]")
        for code in ("ROBIN10", "WELCOME15", "PEWTER20"):
            expect(section.locator("code", has_text=code)).to_be_visible()
        expect(section.locator("code", has_text="NOTYET")).to_have_count(0)
        expect(page.get_by_role("link", name="Request a Code", exact=True)).to_have_count(0)
        _shoot(page, 3, "classes.class-codes")
        section.get_by_role("link", name="See Your Discount Codes →").click()
        expect(page).to_have_url(f"{live_server.url}{reverse('classes:teach_discount_codes')}")
