"""BDD specs for public classes portal — list, detail, category, instructor."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
)
from classes.models import ClassOffering
from membership.models import Member


@pytest.fixture
def published_class(db):
    category = CategoryFactory(name="Ceramics", slug="ceramics")
    instructor = InstructorFactory(full_legal_name="Deenie", instructor_slug="deenie")
    offering = ClassOfferingFactory(
        title="Intro to Wheel Throwing",
        slug="intro-to-wheel-throwing",
        category=category,
        instructor=instructor,
        status=ClassOffering.Status.PUBLISHED,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    return offering


def describe_coerce_dollars_to_cents():
    def it_returns_zero_for_invalid_string():
        from classes.views import _coerce_dollars_to_cents

        assert _coerce_dollars_to_cents("abc") == 0

    def it_returns_zero_for_none():
        from classes.views import _coerce_dollars_to_cents

        assert _coerce_dollars_to_cents(None) == 0

    def it_converts_valid_dollar_amount():
        from classes.views import _coerce_dollars_to_cents

        assert _coerce_dollars_to_cents("12.50") == 1250


def describe_public_list():
    def it_renders_hero_and_published_classes(published_class, client):
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert b"Classes" in response.content
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Deenie" in response.content

    def it_hides_draft_and_pending_classes(published_class, client):
        ClassOfferingFactory(
            title="Secret Draft",
            slug="secret-draft",
            category=published_class.category,
            instructor=published_class.instructor,
            status=ClassOffering.Status.DRAFT,
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Secret Draft" not in response.content

    def it_hides_private_classes(published_class, client):
        private_offering = ClassOfferingFactory(
            title="Private Lesson",
            slug="private-lesson",
            category=published_class.category,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
            is_private=True,
        )
        ClassSessionFactory(
            class_offering=private_offering,
            starts_at=timezone.now() + timedelta(days=4),
            ends_at=timezone.now() + timedelta(days=4, hours=2),
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Private Lesson" not in response.content

    def it_hides_published_classes_with_no_upcoming_sessions(db, client):
        category = CategoryFactory()
        instructor = InstructorFactory()
        ClassOfferingFactory(
            title="Brand New Class",
            slug="brand-new-class",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Brand New Class" not in response.content

    def it_hides_published_classes_whose_only_sessions_are_past(db, client):
        category = CategoryFactory()
        instructor = InstructorFactory()
        stale = ClassOfferingFactory(
            title="Past Class",
            slug="past-class",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        past_start = timezone.now() - timedelta(days=10)
        ClassSessionFactory(
            class_offering=stale,
            starts_at=past_start,
            ends_at=past_start + timedelta(hours=2),
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Past Class" not in response.content

    def it_includes_flexible_classes_even_without_sessions(db, client):
        category = CategoryFactory()
        instructor = InstructorFactory()
        ClassOfferingFactory(
            title="Flexible Workshop",
            slug="flexible-workshop",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
        )
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert b"Flexible Workshop" in response.content

    def it_filters_to_selected_category(published_class, client):
        other_cat = CategoryFactory(name="Blacksmithing", slug="blacksmithing")
        other_offering = ClassOfferingFactory(
            title="Intro to Forging",
            slug="intro-to-forging",
            category=other_cat,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=other_offering,
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?category=ceramics")
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Intro to Forging" not in response.content

    def it_filters_by_instructor_slug(published_class, client):
        other_instructor = InstructorFactory(full_legal_name="Newcomer", instructor_slug="newcomer")
        other = ClassOfferingFactory(
            title="Newcomer Class",
            slug="newcomer-class",
            category=published_class.category,
            instructor=other_instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=other,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?instructor=deenie")
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Newcomer Class" not in response.content

    def it_filters_by_min_and_max_price(published_class, client):
        cheap = ClassOfferingFactory(
            title="Cheap Class",
            slug="cheap-class",
            category=published_class.category,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=500,
        )
        ClassSessionFactory(
            class_offering=cheap,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        # published_class has price_cents=5000 ($50); filter max=$4 to exclude it and include the $5 class
        response = client.get(reverse("classes:public_list") + "?min_price=1&max_price=9")
        assert b"Intro to Wheel Throwing" not in response.content
        assert b"Cheap Class" in response.content

    def it_filters_members_only(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        members_only = ClassOfferingFactory(
            title="Members Class",
            slug="members-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            member_discount_pct=15,
        )
        no_discount = ClassOfferingFactory(
            title="Open Class",
            slug="open-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            member_discount_pct=0,
        )
        ClassSessionFactory(
            class_offering=members_only,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        ClassSessionFactory(
            class_offering=no_discount,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?members_only=1")
        assert b"Members Class" in response.content
        assert b"Open Class" not in response.content

    def it_filters_free_classes(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        free = ClassOfferingFactory(
            title="Free Workshop",
            slug="free-workshop",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=0,
        )
        paid = ClassOfferingFactory(
            title="Paid Workshop",
            slug="paid-workshop",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=2000,
        )
        ClassSessionFactory(
            class_offering=free,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        ClassSessionFactory(
            class_offering=paid,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?free=1")
        assert b"Free Workshop" in response.content
        assert b"Paid Workshop" not in response.content

    def it_filters_upcoming_classes(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        upcoming = ClassOfferingFactory(
            title="Upcoming Class",
            slug="upcoming-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassOfferingFactory(
            title="No Session Class",
            slug="no-session-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=upcoming,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?upcoming=1")
        assert b"Upcoming Class" in response.content
        assert b"No Session Class" not in response.content

    def it_returns_partial_html_for_htmx_requests(published_class, client):
        response = client.get(
            reverse("classes:public_list"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"cp-results__summary" in response.content
        # Partial doesn't include the full page hero
        assert b"<html" not in response.content


def describe_public_category():
    def it_404s_unknown_category(db, client):
        response = client.get(reverse("classes:public_category", kwargs={"slug": "no-such-cat"}))
        assert response.status_code == 404

    def it_renders_only_classes_in_the_category(published_class, client):
        other_cat = CategoryFactory(name="Woodworking", slug="woodworking")
        other_offering = ClassOfferingFactory(
            title="Intro to Chisels",
            slug="intro-to-chisels",
            category=other_cat,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=other_offering,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_category", kwargs={"slug": "ceramics"}))
        assert response.status_code == 200
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Intro to Chisels" not in response.content


def describe_public_class_detail():
    def it_renders_the_detail_page(published_class, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
        assert response.status_code == 200
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Deenie" in response.content
        assert b"Schedule" in response.content
        assert b"2808 SE 9th Ave" in response.content

    def it_404s_on_draft_classes(db, client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, slug="secret")
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.status_code == 404

    def it_404s_on_private_classes(db, client):
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            is_private=True,
            slug="private-one",
        )
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.status_code == 404

    def it_shows_sold_out_when_no_spots_remain(published_class, client):
        from classes.factories import RegistrationFactory
        from classes.models import Registration

        for _ in range(published_class.capacity):
            RegistrationFactory(class_offering=published_class, status=Registration.Status.CONFIRMED)
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
        assert response.status_code == 200
        assert b"Sold out" in response.content


def describe_public_instructor():
    def it_404s_inactive_instructor(db, client):
        instructor = InstructorFactory(instructor_slug="retired", status=Member.Status.FORMER)
        response = client.get(reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug}))
        assert response.status_code == 404

    def it_renders_profile_with_current_classes(published_class, client):
        response = client.get(
            reverse("classes:public_instructor", kwargs={"slug": published_class.instructor.instructor_slug})
        )
        assert response.status_code == 200
        assert b"Deenie" in response.content
        assert b"Intro to Wheel Throwing" in response.content


def describe_google_analytics_gate():
    def it_omits_ga_tag_when_id_not_set(published_class, client):
        response = client.get(reverse("classes:public_list"))
        assert b"googletagmanager.com" not in response.content

    def it_injects_ga_tag_when_id_is_configured(published_class, client):
        from core.models import SiteConfiguration

        site = SiteConfiguration.load()
        site.google_analytics_measurement_id = "G-TEST123"
        site.save()
        response = client.get(reverse("classes:public_list"))
        assert b"googletagmanager.com" in response.content
        assert b"G-TEST123" in response.content
