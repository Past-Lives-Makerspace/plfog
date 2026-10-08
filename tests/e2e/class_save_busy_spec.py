"""End-to-end: Save buttons go busy while the save is in flight, and the admin's Sync to Eventbrite (#713).

The busy state is ``components/busy_submit.html`` reacting to a real boosted submit, which only a
browser runs. Each scenario holds the POST at the network layer, so the button is checked while
the request is genuinely in flight, then lets it through and waits for the page to land.
``CAPTURE_713_SCREENSHOTS=1`` also saves the PR's pictures under ``mockups/screenshots/``.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Route, expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from core.integrations.eventbrite import EventbriteClient, EventbriteSync
from membership.models import Member
from tests.classes.eventbrite_listing_spec import FakeEventbrite
from tests.membership.factories import MembershipPlanFactory

TEACHER = "busy-teacher@example.com"
ADMIN = "busy-admin@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_713_SCREENSHOTS"))
SHOTS = Path("mockups/screenshots")
State = ClassOffering.EventbriteSyncState


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates (see class_composer_draft_spec)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


@pytest.fixture
def eventbrite() -> Iterator[FakeEventbrite]:
    """The live server runs in this process, so the patch reaches the Sync button's request."""
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


def _instructor() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member the instructor factory updates
    user = UserFactory(username=TEACHER, email=TEACHER)
    return cast(Member, InstructorFactory(user=user, full_legal_name="Bea Busy", instructor_slug="bea-busy"))


def _admin() -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=ADMIN, email=ADMIN)
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _hold_posts(page: Page) -> list[Route]:
    """Park every POST the page sends; the caller lets them through with ``route.continue_()``."""
    held: list[Route] = []

    def _park(route: Route) -> None:
        if route.request.method == "POST":
            held.append(route)
        else:
            route.continue_()

    page.route("**/*", _park)
    return held


def _release(page: Page, held: list[Route]) -> None:
    for route in held:
        route.continue_()
    page.unroute("**/*")


def _assert_busy(button: Any) -> None:
    expect(button).to_have_attribute("aria-busy", "true")
    expect(button).to_be_disabled()
    expect(button).to_have_text("Saving...")


def describe_the_published_class_edit_page():
    def it_shows_save_busy_until_the_page_leaves(live_server, page, login_via_code):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED, ready=True)
        login_via_code(TEACHER)
        page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
        save = page.locator("form[data-pl-busy-submit] button[type='submit'][data-pl-busy]")
        expect(save).to_have_text("Save")
        held = _hold_posts(page)

        save.click()

        _assert_busy(save)
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            save.evaluate("el => el.scrollIntoView({block: 'center'})")
            page.screenshot(path=str(SHOTS / "713-busy-save.png"))
        assert len(held) == 1
        _release(page, held)
        expect(page.locator("text=Class updated.")).to_be_visible()


def describe_the_admin_composer():
    def it_shows_save_and_publish_busy_while_the_save_is_in_flight(live_server, page, login_via_code):
        _admin()
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, ready=True)
        login_via_code(ADMIN)
        page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
        bar = page.locator("#composer-form .pl-composer-bar")
        save = bar.locator("button[type='submit'][data-pl-busy]")
        publish = bar.locator("button.pl-composer-bar__primary[data-pl-busy]")
        expect(publish).to_have_text("Publish")
        held = _hold_posts(page)

        save.click()

        _assert_busy(save)
        expect(publish).to_have_attribute("aria-busy", "true")
        expect(publish).to_be_disabled()
        _release(page, held)
        expect(page.locator(".pl-composer")).to_be_visible()
        expect(page.locator("#composer-form .pl-composer-bar button[type='submit']")).not_to_have_attribute(
            "aria-busy", "true"
        )


def describe_the_sync_to_eventbrite_button():
    def it_syncs_now_and_the_row_reads_listed_with_a_check(live_server, page, login_via_code, eventbrite):
        _admin()
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            ready=True,
            eventbrite_enabled=True,
            eventbrite_event_id="ev-9",
            eventbrite_ticket_class_id="tc-9",
            eventbrite_sync_state=State.PENDING,
            eventbrite_sync_error=EventbriteSync.EDIT_SAVED,
        )
        login_via_code(ADMIN)
        page.goto(f"{live_server.url}{reverse('classes:teach_class_detail', kwargs={'pk': offering.pk})}")
        row = page.locator("[data-overview-eventbrite]")
        expect(row).to_contain_text("Waiting to sync: your changes are saved")

        row.locator("[data-eventbrite-sync-button]").click()

        expect(row.locator("[data-eventbrite-sync]")).to_have_text("✓ Listed on Eventbrite")
        expect(row.locator("[data-eventbrite-sync-button]")).to_have_text("Sync to Eventbrite")
        assert "update_event" in eventbrite.names()
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            row.screenshot(path=str(SHOTS / "713-overview-eventbrite-sync.png"))
