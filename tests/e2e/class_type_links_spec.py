"""End-to-end (#1261, part 3 of 3): the catalog's Class Type links and an instructor's Past Classes.

The catalog shows Browse by Class Type, one chip per Class Type with a class to book, wrapping at
phone width rather than scrolling; a chip opens that Class Type's page. An instructor's page lists
Past Classes, linked while a class's page is up and plain text once it is Retired. With
``PL_SCREENSHOT_DIR`` set, both are saved there at the guidance frame (``tests/e2e/guidance.py``)
as ``step-1.png`` and ``step-2.png``. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import ClassOffering
from tests.e2e.guidance import FRAMES, shoot


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates the tables (class_composer_steps_spec has the why)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _run(title: str, category, *, days: int, instructor=None) -> ClassOffering:
    offering = ClassOfferingFactory(
        title=title, slug=title.lower().replace(" ", "-"), category=category, status=ClassOffering.Status.PUBLISHED
    )
    if instructor is not None:
        offering.set_instructors([instructor])
    starts = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=3))
    offering.refresh_from_db()
    offering.class_record.refresh_state()
    return offering


def _shoot(page, step: int, target: str) -> None:
    folder = os.environ.get("PL_SCREENSHOT_DIR")
    if not folder:
        return
    page.set_viewport_size(FRAMES["desktop"])
    page.locator(f'[data-guidance-target="{target}"]').evaluate("(el) => el.scrollIntoView({ block: 'center' })")
    shoot(page, Path(folder) / f"step-{step}.png", target=target)


def describe_class_type_links_and_past_classes():
    def it_links_class_types_and_an_instructors_past_classes(live_server, page):
        teacher = InstructorFactory(full_legal_name="Robin Example", instructor_slug="robin-example")
        glass = CategoryFactory(name="Glass", slug="glass")
        metal = CategoryFactory(name="Metal Casting", slug="metal-casting")
        wood = CategoryFactory(name="Woodworking", slug="woodworking")
        CategoryFactory(name="Leather", slug="leather")
        _run("Pewter Casting Basics", metal, days=10, instructor=teacher)
        _run("Glass Fusing Night", glass, days=12)
        _run("Spoon Carving", wood, days=14)
        _run("Bronze Pour Day", metal, days=-60, instructor=teacher)
        _run("Lost Wax Intro", metal, days=-500, instructor=teacher)

        page.goto(f"{live_server.url}{reverse('classes:public_list')}")
        links = page.locator("[data-class-type-links]")
        expect(links).to_be_visible()
        expect(links.locator("a")).to_have_count(3)
        expect(links).not_to_contain_text("Leather")
        _shoot(page, 1, "classes.class-type-links")

        page.set_viewport_size(FRAMES["phone"])
        width = page.evaluate("() => document.documentElement.scrollWidth")
        assert width <= FRAMES["phone"]["width"]
        links.get_by_role("link", name="Glass").click()
        page.wait_for_url(f"**{reverse('classes:public_category', kwargs={'slug': 'glass'})}")
        expect(page.locator(".pl-type-links__chip--active")).to_contain_text("Glass")

        page.goto(f"{live_server.url}{reverse('classes:public_instructor', kwargs={'slug': 'robin-example'})}")
        past = page.locator('[data-guidance-target="classes.instructor-past-classes"]')
        expect(past.locator("a[data-past-class-link]")).to_contain_text("Bronze Pour Day")
        expect(past.locator("[data-past-class-retired]")).to_contain_text("Lost Wax Intro")
        _shoot(page, 2, "classes.instructor-past-classes")
