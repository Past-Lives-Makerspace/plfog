"""End-to-end (#1261, part 1 of 3): an instructor sets how their class shows in search.

The composer's Review step carries How It Shows In Search: a Search title and a Search
description, each blank with the generated text as its placeholder, capped at 60 and 160
characters. A saved title lands on the class, and the class page's link preview title reads it.
With ``PL_SCREENSHOT_DIR`` set, the step is saved there at the guidance frame
(``tests/e2e/guidance.py``) as ``step-1.png``. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering, ClassRecord
from membership.models import Member
from tests.e2e.guidance import FRAMES, shoot
from tests.membership.factories import MembershipPlanFactory

TEACHER_EMAIL = "search-text-teacher@example.com"
TARGET = "classes.search-text"


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates the tables (class_composer_steps_spec has the why)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _seed(login_via_code) -> ClassOffering:
    MembershipPlanFactory()
    login_via_code(TEACHER_EMAIL)
    member = Member.objects.get(user__username=TEACHER_EMAIL)
    Member.objects.filter(pk=member.pk).update(full_legal_name="Robin Example", preferred_name="")
    member.refresh_from_db()
    member.grant_teaching(granted_by=None)
    return ClassOfferingFactory(
        title="Pewter Casting Basics",
        instructor=member,
        category=CategoryFactory(name="Metal Casting"),
        status=ClassOffering.Status.DRAFT,
        ready=True,
    )


def describe_how_it_shows_in_search():
    def it_saves_a_search_title_to_the_class_from_the_review_step(live_server, page, login_via_code):
        offering = _seed(login_via_code)
        page.goto(f"{live_server.url}{reverse('classes:teach_class_edit', kwargs={'pk': offering.pk})}")
        page.locator('[data-step-tab="6"]').first.click()
        section = page.locator("[data-search-text]")
        expect(section).to_be_visible()
        title = section.locator('input[name="search_title"]')
        expect(title).to_have_attribute("placeholder", offering.seo_title)
        expect(title).to_have_attribute("maxlength", "60")
        expect(section.locator('textarea[name="search_description"]')).to_have_attribute("maxlength", "160")

        folder = os.environ.get("PL_SCREENSHOT_DIR")
        if folder:
            page.set_viewport_size(FRAMES["desktop"])
            section.evaluate("(el) => el.scrollIntoView({ block: 'center' })")
            shoot(page, Path(folder) / "step-1.png", target=TARGET)

        title.fill("Pewter Casting Class in Portland")
        page.locator('#composer-form button[type="submit"]').first.click()
        page.wait_for_load_state("networkidle")
        record = ClassRecord.objects.get(pk=offering.class_record_id)
        assert record.search_title == "Pewter Casting Class in Portland"
