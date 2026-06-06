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


@pytest.fixture
def published_class(db):
    category = CategoryFactory(name="Ceramics", slug="ceramics")
    instructor = InstructorFactory(display_name="Deenie", slug="deenie")
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

    def it_shows_published_classes_with_no_upcoming_sessions(db, client):
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
        assert b"Brand New Class" in response.content
        assert b"Upcoming dates TBA" in response.content

    def it_shows_published_classes_whose_only_sessions_are_past(db, client):
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
        assert b"Past Class" in response.content

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
        instructor = InstructorFactory(slug="retired", is_active=False)
        response = client.get(reverse("classes:public_instructor", kwargs={"slug": instructor.slug}))
        assert response.status_code == 404

    def it_renders_profile_with_current_classes(published_class, client):
        response = client.get(reverse("classes:public_instructor", kwargs={"slug": published_class.instructor.slug}))
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
