"""BDD specs for #823 on the pages: a split class reads as one class with other dates.

The class page lists the runs under other dates, each run's register page offers every date,
a sign up takes a seat on the run it was made on, and an admin who saves several dates on a
live single class gets the same split on that save.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    READY_DESCRIPTION,
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, ClassSession, Registration

pytestmark = pytest.mark.django_db

DAYS = (10, 31, 45, 59)


def _single(*days: int, price_cents: int = 0, **kwargs: Any) -> ClassOffering:
    offering = ClassOfferingFactory(
        title="Make a Cup",
        slug="make-a-cup",
        description=READY_DESCRIPTION,
        price_cents=price_cents,
        capacity=4,
        **kwargs,
    )
    for day in days:
        start = timezone.now() + timedelta(days=day)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _runs(offering: ClassOffering) -> list[ClassOffering]:
    return list(
        ClassOffering.objects.filter(grouping_key=offering.grouping_key)
        .exclude(pk=offering.pk)
        .order_by("sessions__starts_at")
    )


def describe_a_published_split_class():
    @pytest.fixture
    def offering() -> ClassOffering:
        offering = _single(*DAYS)
        offering.publish(UserFactory())
        return offering

    def it_lists_the_later_dates_under_other_dates(offering, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.status_code == 200
        assert [sibling.pk for sibling in response.context["sibling_offerings"]] == [run.pk for run in _runs(offering)]

    def it_offers_every_date_in_each_runs_picker(offering, client):
        everyone = {offering.slug, *(run.slug for run in _runs(offering))}
        for slug in everyone:
            response = client.get(reverse("classes:register", kwargs={"slug": slug}))
            assert {option.slug for option in response.context["run_options"]} == everyone

    def it_takes_a_seat_on_the_run_signed_up_for_only(offering, client):
        third = _runs(offering)[1]
        response = client.post(
            reverse("classes:register", kwargs={"slug": third.slug}),
            {
                "first_name": "Sam",
                "last_name": "Smith",
                "pronouns": "",
                "email": "sam@example.com",
                "phone": "",
                "prior_experience": "",
                "looking_for": "",
                "discount_code": "",
                "liability_signature": "Sam Smith",
                "accepts_liability": "on",
            },
        )
        assert response.status_code == 302
        assert list(Registration.objects.values_list("class_offering_id", flat=True)) == [third.pk]
        assert third.spots_remaining == 3
        assert offering.spots_remaining == 4


def _composer_payload(offering: ClassOffering, *extra_days: int) -> dict[str, Any]:
    """What the admin composer posts: the class's one session kept, plus a new row per ``extra_days``."""
    session = offering.sessions.get()
    payload: dict[str, Any] = {
        "title": offering.title,
        "category": str(offering.category_id),
        "instructors": [str(offering.instructor_id)],
        "description": READY_DESCRIPTION,
        "price_cents": "50.00",
        "capacity": str(offering.capacity),
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "sale_kind": "percent",
        "sessions-TOTAL_FORMS": str(1 + len(extra_days)),
        "sessions-INITIAL_FORMS": "1",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "sessions-0-id": str(session.pk),
        "sessions-0-class_offering": str(offering.pk),
        "sessions-0-starts_at": timezone.localtime(session.starts_at).strftime("%Y-%m-%dT%H:%M"),
        "sessions-0-ends_at": timezone.localtime(session.ends_at).strftime("%Y-%m-%dT%H:%M"),
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }
    for index, day in enumerate(extra_days, start=1):
        start = timezone.localtime(timezone.now() + timedelta(days=day))
        payload[f"sessions-{index}-id"] = ""
        payload[f"sessions-{index}-class_offering"] = str(offering.pk)
        payload[f"sessions-{index}-starts_at"] = start.strftime("%Y-%m-%dT%H:%M")
        payload[f"sessions-{index}-ends_at"] = (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    return payload


def describe_an_admin_saving_several_dates():
    @pytest.fixture
    def live() -> ClassOffering:
        offering = _single(10, price_cents=5000, category=CategoryFactory(), instructor=InstructorFactory())
        offering.publish(UserFactory())
        return offering

    def it_splits_a_published_class_on_that_save(live, admin_user, client):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": live.pk}), _composer_payload(live, 20, 30)
        )
        assert response.status_code == 302, response.context and response.context["form"].errors
        assert ClassSession.objects.filter(class_offering=live).count() == 1
        runs = _runs(live)
        assert [run.sessions.count() for run in runs] == [1, 1]
        assert all(run.status == ClassOffering.Status.PUBLISHED for run in runs)
        assert all(run.approved_by == admin_user for run in runs)

    def it_leaves_the_dates_alone_when_someone_holds_a_seat(live, admin_user, client):
        RegistrationFactory(class_offering=live, status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": live.pk}), _composer_payload(live, 20, 30)
        )
        assert response.status_code == 302
        assert ClassSession.objects.filter(class_offering=live).count() == 3
        assert _runs(live) == []

    def it_leaves_a_draft_to_split_when_it_is_published(admin_user, client):
        draft = _single(10, price_cents=5000, category=CategoryFactory(), instructor=InstructorFactory())
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": draft.pk}), _composer_payload(draft, 20, 30)
        )
        assert response.status_code == 302
        assert ClassSession.objects.filter(class_offering=draft).count() == 3
        assert _runs(draft) == []
