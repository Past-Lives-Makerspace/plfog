"""BDD specs: the instructor page lists classes, and the catalog links Class Types (#1261, part 3 of 3).

The instructor page shows one card per class with a date to come, then Past Classes: each other
class they teach with a public run, linked to its page while that page is up and plain text once
Retired. The catalog links each Class Type with a class to book (a Class Type page lists only
those). Assertions anchor on data attributes and factory strings (STANDARDS.md section 8).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import Category, ClassOffering, ClassRecord
from membership.models import Member

pytestmark = pytest.mark.django_db


def _run(title: str, *, days: int, instructor: Member | None = None, **traits: object) -> ClassOffering:
    traits.setdefault("status", ClassOffering.Status.PUBLISHED)
    offering = ClassOfferingFactory(title=title, slug=title.lower().replace(" ", "-"), **traits)
    if instructor is not None:
        offering.set_instructors([instructor])
    starts = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
    offering.refresh_from_db()
    offering.class_record.refresh_state()
    return offering


def _second_run(first: ClassOffering, *, days: int, instructor: Member) -> ClassOffering:
    run = _run(f"{first.title} Again", days=days, instructor=instructor, category=first.category)
    old_record = run.class_record_id
    ClassOffering.objects.filter(pk=run.pk).update(class_record=first.class_record)
    ClassRecord.objects.filter(pk=old_record).delete()
    first.class_record.refresh_state()
    return run


def _instructor_page(client: Client, instructor: Member) -> str:
    response = client.get(reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug}))
    assert response.status_code == 200
    return response.content.decode()


def describe_the_instructor_page():
    @pytest.fixture
    def instructor() -> Member:
        return InstructorFactory(instructor_slug="qzx-teacher")

    def it_shows_a_class_with_two_dates_once_at_its_soonest(client, instructor):
        first = _run("Qzx Two Dates", days=20, instructor=instructor)
        _second_run(first, days=5, instructor=instructor)
        html = _instructor_page(client, instructor)
        assert html.count(f'href="{first.class_record.public_path}"') == 1

    def it_links_a_class_between_runs_under_past_classes(client, instructor):
        past = _run("Qzx Between Pour", days=-40, instructor=instructor)
        assert past.class_record.state == ClassRecord.State.BETWEEN_RUNS
        html = _instructor_page(client, instructor)
        past_section = html[html.index("<h3>Past Classes</h3>") :]
        assert f'href="{past.class_record.public_path}" data-past-class-link' in past_section

    def it_lists_a_retired_class_as_plain_text(client, instructor):
        retired = _run("Qzx Retired Pour", days=-500, instructor=instructor)
        assert retired.class_record.state == ClassRecord.State.RETIRED
        html = _instructor_page(client, instructor)
        retired_card = html[html.index("data-past-class-retired") :]
        assert retired_card.index("Qzx Retired Pour") < retired_card.index("</div>")
        assert f'href="{retired.class_record.public_path}"' not in html

    def it_treats_a_class_marked_no_longer_offered_as_retired(client, instructor):
        offering = _run("Qzx Set Aside", days=-40, instructor=instructor)
        offering.class_record.mark_no_longer_offered(None)
        html = _instructor_page(client, instructor)
        assert "data-past-class-retired" in html
        assert f'href="{offering.class_record.public_path}"' not in html

    def it_links_a_class_whose_next_date_another_instructor_teaches(client, instructor):
        mine = _run("Qzx Shared Pour", days=-40, instructor=instructor)
        _second_run(mine, days=5, instructor=InstructorFactory(instructor_slug="qzx-other"))
        html = _instructor_page(client, instructor)
        assert f'href="{mine.class_record.public_path}" data-past-class-link' in html

    def it_lists_links_before_plain_text(client, instructor):
        _run("Qzx Aa Retired", days=-500, instructor=instructor)
        between = _run("Qzx Zz Between", days=-40, instructor=instructor)
        html = _instructor_page(client, instructor)
        assert html.index(f'href="{between.class_record.public_path}"') < html.index("data-past-class-retired")

    def it_leaves_out_a_class_with_no_public_run(client, instructor):
        _run("Qzx Only Draft", days=-40, instructor=instructor, status=ClassOffering.Status.DRAFT)
        html = _instructor_page(client, instructor)
        assert "Qzx Only Draft" not in html
        assert "<h3>Past Classes</h3>" not in html


def describe_the_catalog_class_type_links():
    def it_links_each_class_type_with_a_class_to_book(client: Client):
        booking = CategoryFactory(name="Qzx Glass", slug="qzx-glass")
        CategoryFactory(name="Qzx Empty", slug="qzx-empty")
        _run("Qzx Next Pour", days=5, category=booking)
        _run("Qzx Old Pour", days=-40, category=CategoryFactory(name="Qzx Past", slug="qzx-past"))
        html = client.get(reverse("classes:public_list")).content.decode()
        links = html[html.index("data-class-type-links") :]
        links = links[: links.index("</nav>")]
        assert f'href="{reverse("classes:public_category", kwargs={"slug": "qzx-glass"})}"' in links
        assert "qzx-empty" not in links and "qzx-past" not in links

    def it_marks_the_class_type_page_it_is_on(client: Client):
        booking = CategoryFactory(name="Qzx Glass", slug="qzx-glass")
        _run("Qzx Next Pour", days=5, category=booking)
        html = client.get(reverse("classes:public_category", kwargs={"slug": "qzx-glass"})).content.decode()
        assert 'pl-type-links__chip--active" href="/classes/category/qzx-glass/" aria-current="page"' in html

    def it_shows_no_row_with_nothing_to_book(client: Client):
        CategoryFactory(name="Qzx Empty", slug="qzx-empty")
        html = client.get(reverse("classes:public_list")).content.decode()
        assert "data-class-type-links" not in html

    def it_matches_the_class_types_the_sitemap_lists():
        booking = CategoryFactory(name="Qzx Glass", slug="qzx-glass")
        CategoryFactory(name="Qzx Empty", slug="qzx-empty")
        _run("Qzx Next Pour", days=5, category=booking)
        assert list(Category.with_classes_to_book()) == [booking]
