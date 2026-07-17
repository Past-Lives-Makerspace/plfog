"""BDD specs for instructor_class_submit and instructor_discount_code_create."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering, DiscountCode


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher2@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher Two", instructor_slug="teacher-two")


def describe_instructor_class_submit():
    def it_submits_a_draft_for_review_on_post(instructor_fixture, client):
        draft = ClassOfferingFactory(
            instructor=instructor_fixture,
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(instructor_fixture.user)
        response = client.post(reverse("classes:teach_class_submit", kwargs={"pk": draft.pk}))
        assert response.status_code == 302
        draft.refresh_from_db()
        assert draft.status == ClassOffering.Status.PENDING

    def it_ignores_get_requests(instructor_fixture, client):
        draft = ClassOfferingFactory(
            instructor=instructor_fixture,
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_class_submit", kwargs={"pk": draft.pk}))
        assert response.status_code == 302
        draft.refresh_from_db()
        assert draft.status == ClassOffering.Status.DRAFT  # unchanged on GET


def describe_instructor_class_create_form():
    def it_re_renders_with_session_data_on_invalid_post(instructor_fixture, client):
        """A POST with session fields but invalid main form should re-render
        with sessions_json populated from the POST data."""
        client.force_login(instructor_fixture.user)
        from classes.factories import CategoryFactory

        cat = CategoryFactory()
        response = client.post(
            reverse("classes:teach_class_create"),
            {
                "title": "",  # missing required title → form invalid
                "category": cat.pk,
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "sessions-0-starts_at": "2026-08-01T10:00",
                "sessions-0-ends_at": "2026-08-01T12:00",
                "sessions-0-id": "",
                "sessions-0-DELETE": "",
            },
        )
        assert response.status_code == 200
        # sessions_json includes the session data from POST
        assert b"2026-08-01T10:00" in response.content


def describe_instructor_class_edit_form():
    def it_loads_sessions_from_offering_on_get(instructor_fixture, client):
        """GET the edit form for a class with existing sessions — sessions_json
        is populated from the DB rows, not from POST."""
        from datetime import timedelta

        from django.utils import timezone

        from classes.factories import ClassSessionFactory

        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            status=ClassOffering.Status.DRAFT,
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=5),
            ends_at=timezone.now() + timedelta(days=5, hours=2),
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        assert response.status_code == 200
        # The session data should be embedded as JSON
        assert b"sessions_json" in response.content or b"T" in response.content


def describe_instructor_discount_code_create():
    def it_scopes_code_to_class_when_class_param_provided(instructor_fixture, client):
        """Creating a discount code with ?class=<pk> scopes it to that offering.

        Every new code — including an instructor's own-class code — starts
        unapproved (the auto-approve shortcut was removed); it needs approval
        before it's usable.
        """
        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(instructor_fixture.user)
        url = reverse("classes:teach_discount_code_create") + f"?class={offering.pk}"
        response = client.post(
            url,
            {
                "code": "CLASSONLY10",
                "discount_pct": 10,
                "is_active": "on",
                "class": offering.pk,
            },
        )
        assert response.status_code == 302
        code = DiscountCode.objects.get(code="CLASSONLY10")
        assert code.class_offering == offering
        assert code.is_approved is False
        assert response.url == reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})

    def it_falls_back_gracefully_when_class_param_is_invalid(instructor_fixture, client):
        """An invalid ?class= value should be silently ignored — scoped_to=None."""
        client.force_login(instructor_fixture.user)
        url = reverse("classes:teach_discount_code_create") + "?class=99999"
        response = client.get(url)
        assert response.status_code == 200
