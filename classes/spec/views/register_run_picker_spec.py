"""BDD specs for the date-set chooser on the registration page.

When the same class is offered on more than one still-bookable set of dates, the
register page shows a dropdown so the registrant can switch runs. Started runs
never appear (you can't join part-way through).
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
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


@pytest.fixture
def group(db):
    return {"category": CategoryFactory(name="Metal", slug="metal"), "instructor": InstructorFactory()}


def _run(group, slug, *offsets_days, title="Blacksmithing 101"):
    scheduling = (
        ClassOffering.SchedulingType.SERIES_PACKAGE
        if len(offsets_days) > 1
        else ClassOffering.SchedulingType.SINGLE_SESSION
    )
    offering = ClassOfferingFactory(
        title=title,
        slug=slug,
        category=group["category"],
        instructor=group["instructor"],
        status=ClassOffering.Status.PUBLISHED,
        scheduling_type=scheduling,
    )
    base = timezone.now()
    for days in offsets_days:
        start = base + timedelta(days=days)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_run_picker():
    def it_offers_a_dropdown_listing_every_bookable_run(group, client):
        sep = _run(group, "bs-sep", 7, 14, 21)
        _run(group, "bs-oct", 40, 47, 54)

        response = client.get(reverse("classes:register", kwargs={"slug": sep.slug}))
        body = response.content.decode()

        assert response.status_code == 200
        assert {o.slug for o in response.context["run_options"]} == {"bs-sep", "bs-oct"}
        assert 'id="reg-run-select"' in body
        assert reverse("classes:register", kwargs={"slug": "bs-oct"}) in body
        # The run being viewed is the pre-selected option.
        assert f'value="{reverse("classes:register", kwargs={"slug": "bs-sep"})}" selected' in body

    def it_excludes_started_runs_from_the_dropdown(group, client):
        sep = _run(group, "bs-sep", 7, 14, 21)
        _run(group, "bs-oct", 40, 47, 54)
        _run(group, "bs-started", -2, 5, 12)  # first date passed — not bookable

        response = client.get(reverse("classes:register", kwargs={"slug": sep.slug}))

        slugs = {o.slug for o in response.context["run_options"]}
        assert slugs == {"bs-sep", "bs-oct"}
        assert "bs-started" not in slugs

    def it_shows_no_dropdown_for_a_class_with_a_single_bookable_run(group, client):
        solo = _run(group, "solo-weld", 7, title="One-Off Welding")

        response = client.get(reverse("classes:register", kwargs={"slug": solo.slug}))

        assert response.context["run_options"] == []
        assert 'id="reg-run-select"' not in response.content.decode()
