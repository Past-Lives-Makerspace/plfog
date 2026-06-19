"""BDD specs for the series label + date-set grouping on public catalog cards and detail pages."""

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


# Target the rendered badge markup, not the bare phrase: the public pages also
# render the release changelog ("What's new"), which mentions "multi-session
# series" in prose — so a bare substring check would match that, not the badge.
SERIES_BADGE = '<span class="badge series">Multi-session series</span>'


def describe_catalog_card_badges():
    def it_shows_a_multi_session_series_label_on_series_cards(published_series, client):
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert SERIES_BADGE in response.content.decode()

    def it_does_not_label_single_cards_as_a_series(published_single, client):
        response = client.get(reverse("classes:public_list"))
        body = response.content.decode()
        assert response.status_code == 200
        assert "One-Off Welding" in body
        assert SERIES_BADGE not in body

    def it_collapses_runs_of_the_same_series_into_one_card(published_series, client):
        # A second run of the same series (different dates) shares the grouping
        # key, so the catalog shows ONE card offering both date-sets as options.
        SeriesClassOfferingFactory(
            title="Blacksmithing 101",
            slug="blacksmithing-101-july",
            category=published_series.category,
            instructor=published_series.instructor,
            status=ClassOffering.Status.PUBLISHED,
            session_count=3,
        )
        response = client.get(reverse("classes:public_list"))
        body = response.content.decode()
        assert '<div class="cls-schedule__pick">Pick a session set:</div>' in body
        assert "2 options" in body


def describe_detail_page_badges():
    def it_shows_a_multi_session_series_label_on_a_series_detail_page(published_series, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_series.slug}))
        assert response.status_code == 200
        body = response.content.decode()
        assert SERIES_BADGE in body
        assert "3 sessions" in body

    def it_does_not_label_a_single_detail_page_as_a_series(published_single, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_single.slug}))
        assert response.status_code == 200
        assert SERIES_BADGE not in response.content.decode()
