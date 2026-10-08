"""End-to-end: Eventbrite on the composer's dates step, the opt in (#652) and the category (#716).

The two fields sit inside the Fixed block of step 3, so they leave with it when the class is
switched to Flexible; that is Alpine's x-show, which only a browser runs. Saving keeps both.
``CAPTURE_652_SCREENSHOT=1`` also saves the PR's picture under ``mockups/screenshots/``.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "eventbrite-teacher@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_652_SCREENSHOT"))
SHOTS = Path("mockups/screenshots")
SECTION = "[data-eventbrite]"
SAVE_DRAFT = '#composer-form button[type="submit"]'


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the Review step's preview finish loading before teardown truncates (see class_composer_draft_spec)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _seed(settings: Any) -> ClassOffering:
    # The fields show only while the integration is on: the site toggle plus every credential.
    settings.EVENTBRITE_PRIVATE_TOKEN = "token"
    settings.EVENTBRITE_ORGANIZATION_ID = "org"
    settings.EVENTBRITE_VENUE_ID = "venue"
    config = SiteConfiguration.load()
    config.eventbrite_sync_enabled = True
    config.save(update_fields=["eventbrite_sync_enabled"])
    MembershipPlanFactory()  # so the user signal provisions the member the instructor factory updates
    user = UserFactory(username=EMAIL, email=EMAIL)
    instructor = cast(Member, InstructorFactory(user=user, full_legal_name="Eve Brightwater", instructor_slug="eve"))
    return cast(
        ClassOffering,
        ClassOfferingFactory(instructor=instructor, title="Intro to Lost Wax Casting", price_cents=8000, ready=True),
    )


def describe_eventbrite_fields_on_the_instructor_composer():
    def it_saves_the_opt_in_and_hides_it_for_a_flexible_class(live_server, page, login_via_code, settings):
        offering = _seed(settings)
        login_via_code(EMAIL)
        page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}?step=3")
        section = page.locator(SECTION)
        expect(section).to_be_visible()
        expect(section).to_contain_text("On a $80 ticket that is about $7.07.")

        section.locator("label.pl-toggle").filter(has=page.locator("input[name='eventbrite_enabled']")).click()
        page.select_option("select[name='eventbrite_fee_payer']", ClassOffering.EventbriteFeePayer.INCLUDED)
        expect(page.locator("input[name='eventbrite_enabled']")).to_be_checked()
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            section.screenshot(path=str(SHOTS / "652-instructor-eventbrite-fields.png"))

        page.select_option("select[name='scheduling_model']", ClassOffering.SchedulingModel.FLEXIBLE)
        expect(section).to_be_hidden()
        page.select_option("select[name='scheduling_model']", ClassOffering.SchedulingModel.FIXED)
        expect(section).to_be_visible()

        page.locator(SAVE_DRAFT).click()
        page.get_by_text("Draft saved.").wait_for()
        expect(page.locator("input[name='eventbrite_enabled']")).to_be_checked()
        offering.refresh_from_db()
        assert offering.eventbrite_enabled is True
        assert offering.eventbrite_fee_payer == ClassOffering.EventbriteFeePayer.INCLUDED


def describe_eventbrite_category_on_the_instructor_composer():
    """#716: the subcategory list narrows to the chosen category in the browser (Alpine), and the pair saves."""

    def it_offers_only_the_chosen_categorys_subcategories_and_saves_the_pair(
        live_server, page, login_via_code, settings
    ):
        offering = _seed(settings)
        login_via_code(EMAIL)
        page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}?step=3")
        category = page.locator("select[name='eventbrite_category']")
        subcategory = page.locator("select[name='eventbrite_subcategory']")
        jewelry, diy = subcategory.locator("option[value='5014']"), subcategory.locator("option[value='19003']")
        expect(jewelry).to_be_disabled()

        category.select_option("105")
        expect(jewelry).to_be_enabled()
        expect(diy).to_be_disabled()
        subcategory.select_option("5014")

        category.select_option("119")
        expect(subcategory).to_have_value("")
        expect(jewelry).to_be_disabled()
        expect(diy).to_be_enabled()
        subcategory.select_option("19003")

        page.locator(SAVE_DRAFT).click()
        page.get_by_text("Draft saved.").wait_for()
        expect(subcategory).to_have_value("19003")
        expect(jewelry).to_be_disabled()
        offering.refresh_from_db()
        assert (offering.eventbrite_category, offering.eventbrite_subcategory) == ("119", "19003")
