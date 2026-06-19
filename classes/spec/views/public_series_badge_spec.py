"""BDD specs for the series/single badge on public catalog cards and detail pages."""

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
    SeriesClassOfferingFactory,
)
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


@pytest.fixture
def published_series(db):
    offering = SeriesClassOfferingFactory(
        title="Blacksmithing 101",
        slug="blacksmithing-101",
        category=CategoryFactory(name="Metal", slug="metal"),
        instructor=InstructorFactory(full_legal_name="Glen", instructor_slug="glen"),
        status=ClassOffering.Status.PUBLISHED,
        session_count=3,
    )
    return offering


@pytest.fixture
def published_single(db):
    offering = ClassOfferingFactory(
        title="One-Off Welding",
        slug="one-off-welding",
        category=CategoryFactory(name="Welding", slug="welding"),
        instructor=InstructorFactory(full_legal_name="Pat", instructor_slug="pat"),
        status=ClassOffering.Status.PUBLISHED,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=5),
        ends_at=timezone.now() + timedelta(days=5, hours=2),
    )
    return offering


def describe_catalog_card_badges():
    def it_shows_a_series_badge_on_series_cards(published_series, client):
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert "Series Package" in response.content.decode()

    def it_shows_a_single_badge_on_single_cards(published_single, client):
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert "Single Session" in response.content.decode()

    def it_does_not_collapse_a_series_with_same_title_singles(published_series, client):
        # A same-title single offering must remain its own card — the series
        # stands alone (blank grouping_key) so the two never merge.
        single = ClassOfferingFactory(
            title="Blacksmithing 101",
            slug="blacksmithing-101-single",
            category=published_series.category,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=single,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list"))
        body = response.content.decode()
        assert "Series Package" in body
        assert "Single Session" in body


def describe_detail_page_badges():
    def it_shows_a_series_badge_on_a_series_detail_page(published_series, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_series.slug}))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Series Package" in body
        assert "3 sessions" in body

    def it_shows_a_single_badge_on_a_single_detail_page(published_single, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_single.slug}))
        assert response.status_code == 200
        assert "Single Session" in response.content.decode()
