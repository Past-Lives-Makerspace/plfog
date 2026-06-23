"""BDD specs for the public booking window across catalog, detail, and register.

A dated class — single or series — must drop out of every booking surface the
instant its first session begins. You can't join a series part-way through.
"""

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
from classes.models import ClassOffering, Registration

pytestmark = pytest.mark.django_db


def _series(slug, title, *offsets_days, status=ClassOffering.Status.PUBLISHED):
    offering = ClassOfferingFactory(
        title=title,
        slug=slug,
        category=CategoryFactory(name=title, slug=f"{slug}-cat"),
        instructor=InstructorFactory(),
        status=status,
        scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE,
    )
    base = timezone.now()
    for days in offsets_days:
        start = base + timedelta(days=days)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_catalog():
    def it_hides_a_started_series_but_keeps_a_future_one(client):
        _series("started-series", "Started Forge Series", -3, 4, 11)
        _series("future-series", "Future Forge Series", 7, 14, 21)
        body = client.get(reverse("classes:public_list")).content.decode()
        assert "Future Forge Series" in body
        assert "Started Forge Series" not in body


def describe_register():
    def it_redirects_a_started_class_to_its_detail_page(client):
        series = _series("late-series", "Late Series", -1, 6, 13)
        response = client.get(reverse("classes:register", kwargs={"slug": series.slug}))
        assert response.status_code == 302
        assert response.url == reverse("classes:public_class_detail", kwargs={"slug": series.slug})

    def it_does_not_create_a_registration_for_a_started_class(client):
        series = _series("late-series-2", "Late Series Two", -1, 6, 13)
        client.post(
            reverse("classes:register", kwargs={"slug": series.slug}),
            data={"first_name": "Sam", "last_name": "Smith", "email": "sam@example.com"},
        )
        assert not Registration.objects.filter(class_offering=series).exists()


def describe_detail_of_a_started_series():
    @pytest.fixture
    def started(client):
        return _series("started-detail", "Started Detail Series", -3, 4, 11)

    def it_shows_registration_closed_instead_of_a_cta(started, client):
        body = client.get(reverse("classes:public_class_detail", kwargs={"slug": started.slug})).content.decode()
        assert "Registration closed" in body
        assert "has already started" in body
        assert "Register now" not in body

    def it_still_shows_the_full_series_schedule_with_past_dates_marked(started, client):
        body = client.get(reverse("classes:public_class_detail", kwargs={"slug": started.slug})).content.decode()
        # All three dates are listed (not collapsed to the one upcoming session)...
        assert "3 sessions" in body
        # ...and the date that already happened is marked as such.
        assert "already happened" in body
