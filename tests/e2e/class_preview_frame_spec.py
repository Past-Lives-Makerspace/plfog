"""End-to-end: the class composer's framed preview keeps its links out of the frame (#1217).

The Review step frames ``classes:class_preview`` with ``?framed=1``. The hub body is boosted,
so before #1217 a click inside the frame swapped the whole portal into it, and Refresh Preview
then reloaded that page, which every view but the preview refuses to frame. What only a
browser can show: the click replaces the composer page itself, and Refresh Preview brings the
class back after the frame was sent somewhere else. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re
from typing import cast

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from tests.membership.factories import MembershipPlanFactory

EMAIL = "frame-teacher@example.com"
FRAME = ".pl-wizard-preview__iframe"


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the lazy preview frame finish loading before the teardown truncates the tables.

    Same deadlock as ``class_composer_steps_spec.py``: a request still in flight holds row
    locks the truncate then loses to. A Playwright error is swallowed on purpose.
    """
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _seed_draft() -> ClassOffering:
    MembershipPlanFactory()  # so the user signal can provision the member the instructor factory then updates
    user = UserFactory(username=EMAIL)
    instructor = InstructorFactory(user=user, full_legal_name="Frame Teacher", instructor_slug="frame-teacher")
    return cast(
        ClassOffering,
        ClassOfferingFactory(
            instructor=instructor, status=ClassOffering.Status.DRAFT, ready=True, title="Framed Forge Basics"
        ),
    )


def _open_review_preview(page: Page, live_server, offering: ClassOffering) -> None:
    """Open the composer on the saved draft, go to its last step and wait for the class inside the frame."""
    page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
    page.locator("[data-step-tab]").last.click()
    expect(page.locator(FRAME)).to_be_visible()
    expect(page.frame_locator(FRAME).locator(".cp-detail__title")).to_contain_text(offering.title)


def describe_a_click_inside_the_preview():
    def it_opens_the_page_in_the_whole_window(live_server, page, login_via_code):
        offering = _seed_draft()
        login_via_code(EMAIL)
        _open_review_preview(page, live_server, offering)

        category_url = reverse("classes:public_category", kwargs={"slug": offering.category.slug})
        page.frame_locator(FRAME).locator(".cp-detail__cat-chip").click()

        # The composer page itself is replaced; nothing was swapped into the frame.
        page.wait_for_url(re.compile(re.escape(category_url) + r"$"))
        expect(page.locator(FRAME)).to_have_count(0)


def describe_refresh_preview():
    def it_shows_the_class_again_after_the_frame_was_sent_elsewhere(live_server, page, login_via_code):
        offering = _seed_draft()
        login_via_code(EMAIL)
        _open_review_preview(page, live_server, offering)
        frame = page.frame_locator(FRAME)

        # Send the frame to a page that refuses to be framed, as a boosted click used to.
        page.evaluate(f"document.querySelector('{FRAME}').contentWindow.location.href = '{reverse('hub_home')}'")
        expect(frame.locator(".cp-detail__title")).to_have_count(0)

        page.get_by_role("button", name="Refresh Preview").click()

        expect(frame.locator(".cp-detail__title")).to_contain_text(offering.title)
        assert page.url.endswith(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
