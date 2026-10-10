"""End-to-end: the Eventbrite tab on Manage this class (#725 part 2).

Validate reads the saved class over htmx and Submit appears only after a pass (Alpine); the
Published switch opens the confirm modal; only a browser runs those. Eventbrite itself is the
listing specs' :class:`FakeEventbrite`, patched in process, so the live server never reaches it.
``CAPTURE_725B_SCREENSHOT=1`` saves the PR's six pictures under ``mockups/screenshots/``.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from core.integrations.eventbrite import EventbriteClient, EventbriteSync
from membership.models import Member
from tests.classes.eventbrite_listing_spec import FakeEventbrite
from tests.membership.factories import MembershipPlanFactory

EMAIL = "eventbrite-tab@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_725B_SCREENSHOT"))
SHOTS = Path("mockups/screenshots")
TAB = "[data-eventbrite-tab]"


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the page finish its requests before teardown truncates (see class_composer_draft_spec)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


@pytest.fixture
def eventbrite() -> Iterator[FakeEventbrite]:
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


def _seed(**kwargs: Any) -> ClassOffering:
    MembershipPlanFactory()  # so the user signal provisions the member the instructor factory updates
    user = UserFactory(username=EMAIL, email=EMAIL)
    instructor = cast(Member, InstructorFactory(user=user, full_legal_name="Eve Brightwater", instructor_slug="eve"))
    fields = {
        "instructor": instructor,
        "title": "Intro to Lost Wax Casting",
        "price_cents": 8000,
        "ready": True,
        "status": ClassOffering.Status.PUBLISHED,
        **kwargs,
    }
    return cast(ClassOffering, ClassOfferingFactory(**fields))


def _open(page: Any, live_server: Any, offering: ClassOffering) -> Any:
    if CAPTURE:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.set_viewport_size({"width": 1280, "height": 1500})
    page.goto(f"{live_server.url}{reverse('classes:teach_class_eventbrite', kwargs={'pk': offering.pk})}")
    return page.locator(TAB)


def _shot(page: Any, section: Any, name: str) -> None:
    if CAPTURE:
        page.wait_for_timeout(300)  # badges and toggles settle
        section.screenshot(path=str(SHOTS / f"725b-eventbrite-tab-{name}.png"))


def describe_the_eventbrite_tab():
    def it_validates_submits_lists_and_unpublishes_through_the_modal(
        live_server, page, login_via_code, eventbrite: FakeEventbrite
    ):
        offering = _seed()
        login_via_code(EMAIL)
        section = _open(page, live_server, offering)
        submit = section.locator("[data-eventbrite-submit]")
        expect(section.locator("[data-eventbrite-stage]")).to_contain_text("Not on Eventbrite")
        expect(submit).to_be_hidden()
        _shot(page, section, "01")

        section.locator("[data-eventbrite-validate]").click()
        expect(section.locator("[data-eventbrite-ready]")).to_contain_text("Ready for Eventbrite")
        expect(submit).to_be_visible()
        _shot(page, section, "03")

        submit.click()
        expect(section.locator("[data-eventbrite-stage]")).to_contain_text("Listed on Eventbrite")
        switch = page.locator("input[name='published']")
        expect(switch).to_be_checked()
        _shot(page, section, "04")
        offering.refresh_from_db()
        assert (offering.eventbrite_enabled, offering.eventbrite_published) == (True, True)
        assert offering.eventbrite_rules_agreed_at is not None

        section.locator("label.pl-toggle").filter(has=switch).click()
        modal = page.locator(".pl-modal").filter(has_text="Unpublish from Eventbrite?")
        expect(modal).to_be_visible()
        expect(modal).to_contain_text("The event goes back to a draft on Eventbrite")
        expect(switch).to_be_checked()  # nothing changes until Confirm
        if CAPTURE:
            page.wait_for_timeout(300)
            page.screenshot(path=str(SHOTS / "725b-eventbrite-tab-05.png"))

        modal.get_by_role("button", name="Unpublish").click()
        expect(page.locator(TAB).locator("[data-eventbrite-stage]")).to_contain_text("Not on Eventbrite")
        offering.refresh_from_db()
        assert (offering.eventbrite_enabled, offering.eventbrite_published) == (False, False)
        assert eventbrite.names()[-1] == "unpublish"

    def it_keeps_submit_hidden_when_the_listing_fails(live_server, page, login_via_code, eventbrite: FakeEventbrite):
        offering = _seed()
        ClassOffering.objects.filter(pk=offering.pk).update(
            description="<p>Learn lost wax casting. Materials are paid at the session (cash/venmo).</p>"
        )
        login_via_code(EMAIL)
        section = _open(page, live_server, offering)

        section.locator("[data-eventbrite-validate]").click()

        refusal = section.locator("[data-eventbrite-refusal]")
        expect(refusal).to_contain_text("Fix these, then save again.")
        expect(refusal).to_contain_text("Description: payment outside the ticket")
        expect(section.locator("[data-eventbrite-submit]")).to_be_hidden()
        _shot(page, section, "02")
        assert eventbrite.calls == []

    def it_shows_a_taken_down_listing_with_nothing_to_submit(
        live_server, page, login_via_code, eventbrite: FakeEventbrite
    ):
        offering = _seed(
            eventbrite_enabled=True,
            eventbrite_event_id="ev-9",
            eventbrite_ticket_class_id="tc-9",
            eventbrite_published=True,
            eventbrite_sync_state=ClassOffering.EventbriteSyncState.ENDED,
            eventbrite_sync_error=EventbriteSync.TAKEN_DOWN,
        )
        login_via_code(EMAIL)
        section = _open(page, live_server, offering)

        expect(section.locator("[data-eventbrite-stage]")).to_contain_text("Taken down")
        expect(section.locator("[data-eventbrite-taken-down]")).to_be_visible()
        expect(section.locator("[data-eventbrite-validate]")).to_have_count(0)
        expect(page.locator("input[name='published']")).to_have_count(0)
        _shot(page, section, "06")
        assert eventbrite.calls == []
