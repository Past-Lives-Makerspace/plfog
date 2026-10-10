"""End-to-end: Submit to Eventbrite on the composer's last page (#725), with the fee and category (#652, #716).

Ticking the box reveals the fee and the category and checks the class's text as it is typed;
that is Alpine's x-show and htmx, which only a browser runs. The section is offered for a fixed
class only. Saving keeps all of it. ``CAPTURE_652_SCREENSHOT=1`` also saves the PR's picture under
``mockups/screenshots/``, and ``CAPTURE_725_SCREENSHOT=1`` the four of #725: the last page
unticked, ticked and ready, ticked and refused, and the email a class that fails as it goes live sends.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "eventbrite-teacher@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_652_SCREENSHOT"))
CAPTURE_725 = bool(os.environ.get("CAPTURE_725_SCREENSHOT"))
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
    settings.EVENTBRITE_ORGANIZER_ID = "organizer-1"
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


def _tick(section: Any, name: str) -> None:
    """Flip a switch in the section: its input is hidden under the toggle's label."""
    section.locator("label.pl-toggle").filter(has=section.page.locator(f"input[name='{name}']")).click()


def _last_page(page: Any, live_server: Any, offering: ClassOffering) -> Any:
    page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}?step=6")
    return page.locator(SECTION)


def describe_eventbrite_fields_on_the_instructor_composer():
    def it_saves_submit_to_eventbrite_and_hides_it_for_a_flexible_class(live_server, page, login_via_code, settings):
        offering = _seed(settings)
        login_via_code(EMAIL)
        section = _last_page(page, live_server, offering)
        expect(section).to_be_visible()
        expect(section.locator("[data-eventbrite-details]")).to_be_hidden()

        _tick(section, "eventbrite_enabled")
        expect(section).to_contain_text("On a $80 ticket that is about $7.07.")
        page.select_option("select[name='eventbrite_fee_payer']", ClassOffering.EventbriteFeePayer.INCLUDED)
        expect(section.locator("[data-eventbrite-ready]")).to_be_visible()
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            section.screenshot(path=str(SHOTS / "652-instructor-eventbrite-fields.png"))

        page.locator("[data-step-tab='3']").click()
        page.select_option("select[name='scheduling_model']", ClassOffering.SchedulingModel.FLEXIBLE)
        page.locator("[data-step-tab='6']").click()
        expect(section).to_be_hidden()
        page.locator("[data-step-tab='3']").click()
        page.select_option("select[name='scheduling_model']", ClassOffering.SchedulingModel.FIXED)
        page.locator("[data-step-tab='6']").click()
        expect(section).to_be_visible()

        page.locator(SAVE_DRAFT).click()
        page.get_by_text("Draft saved.").wait_for()
        offering.refresh_from_db()
        assert offering.eventbrite_enabled is True
        assert offering.eventbrite_fee_payer == ClassOffering.EventbriteFeePayer.INCLUDED
        assert offering.eventbrite_rules_agreed_at is not None


def describe_eventbrite_category_on_the_instructor_composer():
    """#716: the subcategory list narrows to the chosen category in the browser (Alpine), and the pair saves."""

    def it_offers_only_the_chosen_categorys_subcategories_and_saves_the_pair(
        live_server, page, login_via_code, settings
    ):
        offering = _seed(settings)
        login_via_code(EMAIL)
        section = _last_page(page, live_server, offering)
        _tick(section, "eventbrite_enabled")
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


def describe_submit_to_eventbrite_checks_as_you_type():
    """#725 AC8: ticked, the class is checked as typed; red names every problem, green once it is fixed."""

    def it_shows_red_then_green_after_a_fix_and_saves(live_server, page, login_via_code, settings):
        offering = _seed(settings)
        ClassOffering.objects.filter(pk=offering.pk).update(title="Intro to Lost Wax Casting @covo.studio")
        login_via_code(EMAIL)
        if CAPTURE_725:
            page.set_viewport_size({"width": 1280, "height": 1400})  # the whole section above the sticky bar
        section = _last_page(page, live_server, offering)
        expect(section.locator("[data-eventbrite-rules]")).to_contain_text("Eventbrite takes down listings")
        expect(section.locator("[data-eventbrite-details]")).to_be_hidden()
        if CAPTURE_725:
            SHOTS.mkdir(parents=True, exist_ok=True)
            section.screenshot(path=str(SHOTS / "725-eventbrite-rules-01.png"))

        _tick(section, "eventbrite_enabled")
        refusal = section.locator("[data-eventbrite-refusal]")
        expect(refusal).to_contain_text("Fix these, then save again.")
        expect(refusal).to_contain_text("Title: a link, email, phone number or handle in the title: “@covo.studio”")
        if CAPTURE_725:
            page.wait_for_timeout(400)  # the toggle slides; capture it settled
            section.screenshot(path=str(SHOTS / "725-eventbrite-rules-03.png"))

        page.locator("[data-step-tab='1']").click()
        page.fill("input[name='title']", "Intro to Lost Wax Casting")
        page.locator("[data-step-tab='6']").click()
        expect(section.locator("[data-eventbrite-ready]")).to_contain_text("Ready for Eventbrite")
        expect(refusal).to_have_count(0)
        expect(page.locator("select[name='eventbrite_fee_payer']")).to_be_visible()
        if CAPTURE_725:
            section.screenshot(path=str(SHOTS / "725-eventbrite-rules-02.png"))

        page.locator(SAVE_DRAFT).click()
        page.get_by_text("Draft saved.").wait_for()
        offering.refresh_from_db()
        assert (offering.title, offering.eventbrite_enabled) == ("Intro to Lost Wax Casting", True)
        assert offering.eventbrite_rules_agreed_at is not None

    def it_renders_the_email_a_class_that_fails_as_it_goes_live_sends(page, settings):
        """The picture of the email (#725 -04): a queued class that fails the check when it publishes."""
        from django.core import mail

        offering = _seed(settings)
        ClassOffering.objects.filter(pk=offering.pk).update(
            eventbrite_enabled=True,
            eventbrite_rules_agreed_at=timezone.now(),
            description="<p>Materials are paid at the session (cash/venmo).</p>",
        )
        offering.refresh_from_db()
        # The check fails before any Eventbrite call, so nothing here reaches Eventbrite.
        offering.publish(UserFactory(username="eb-e2e-admin@example.com"))

        [email] = [message for message in mail.outbox if "was not listed on Eventbrite" in message.subject]
        html = email.alternatives[0][0]
        assert "Request a Change" in html
        if CAPTURE_725:
            page.set_content(html)
            page.screenshot(path=str(SHOTS / "725-eventbrite-rules-04.png"), full_page=True)
