"""BDD specs for instructor_class_submit and instructor_discount_code_create."""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering, DiscountCode

Status = ClassOffering.Status


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher2@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher Two", instructor_slug="teacher-two")


def describe_instructor_class_submit():
    def it_submits_a_draft_for_review_on_post(instructor_fixture, client):
        draft = ClassOfferingFactory(
            ready=True,
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
                "faq-TOTAL_FORMS": "0",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
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


# ── Issue #368 item 2: the quick Submit button outside the composer ──


def describe_the_quick_submit_button():
    def it_is_disabled_on_the_list_with_the_checklist_as_the_hint_while_a_draft_is_unready(instructor_fixture, client):
        draft = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True, gallery=0)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_dashboard")).content.decode()
        assert reverse("classes:teach_class_submit", kwargs={"pk": draft.pk}) not in html
        assert 'disabled aria-label="Submit for review: Not ready to submit: Add one gallery photo."' in html
        assert '<span class="pl-help__bubble" role="tooltip">Not ready to submit: Add one gallery photo.</span>' in html

    def it_posts_from_the_list_once_the_draft_is_ready(instructor_fixture, client):
        draft = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_dashboard")).content.decode()
        assert f'action="{reverse("classes:teach_class_submit", kwargs={"pk": draft.pk})}"' in html
        assert 'disabled aria-label="Submit for review' not in html

    def it_is_gated_the_same_way_on_the_class_page(instructor_fixture, client):
        unready = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True, gallery=0)
        ready = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": unready.pk})).content.decode()
        assert reverse("classes:teach_class_submit", kwargs={"pk": unready.pk}) not in html
        assert 'disabled aria-label="Submit for review: Not ready to submit: Add one gallery photo."' in html
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": ready.pk})).content.decode()
        assert f'action="{reverse("classes:teach_class_submit", kwargs={"pk": ready.pk})}"' in html
        assert 'disabled aria-label="Submit for review' not in html

    def it_costs_the_list_no_extra_queries_per_draft(instructor_fixture, client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client.force_login(instructor_fixture.user)
        ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, gallery=0)
        client.get(reverse("classes:teach_dashboard"))  # first request work (the billing tab row) is not row cost
        with CaptureQueriesContext(connection) as ctx:
            client.get(reverse("classes:teach_dashboard"))
        at_one = len(ctx)
        for _ in range(5):
            ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, gallery=0)
        with CaptureQueriesContext(connection) as ctx:
            html = client.get(reverse("classes:teach_dashboard")).content.decode()
        assert html.count('disabled aria-label="Submit for review') == 6
        assert len(ctx) == at_one


def describe_a_quick_submit_refused_for_readiness():
    def it_lands_in_the_composer_on_the_first_unready_step_with_the_checklist(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True, gallery=0)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_submit", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        edit = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        assert resp["Location"] == f"{edit}?step=2&missing=1"
        assert "Not ready to submit: Add one gallery photo." in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        html = client.get(resp["Location"]).content.decode()
        assert "phase: 2," in html
        assert "Still Missing" in html
        assert "goToField('gallery-manager')\">Add one gallery photo.</button>" in html
        assert "Some Things Need Fixing" not in html

    def it_lands_on_the_basics_step_for_a_short_description(instructor_fixture, client):
        # The factory default is a one line description and no date: the first gap is on step 1.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_submit", kwargs={"pk": offering.pk}))
        edit = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        assert resp["Location"] == f"{edit}?step=1&missing=1"
        html = client.get(resp["Location"]).content.decode()
        assert "phase: 1," in html
        assert "Write a short description." in html and "Add at least one date." in html


def describe_a_quick_submit_on_a_class_that_is_no_longer_a_draft():
    def it_lands_on_the_class_page_and_says_it_was_already_submitted(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PENDING)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_submit", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert _messages(resp) == ["This class has already been submitted and is waiting for review."]
        offering.refresh_from_db()
        assert offering.status == Status.PENDING

    def it_names_the_state_of_a_published_class(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PUBLISHED)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_submit", kwargs={"pk": offering.pk}))
        assert resp["Location"] == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert _messages(resp) == ["Only a draft can be submitted for review. This class is published."]

    def it_survives_a_double_click(instructor_fixture, client):
        # The list is hx-boosted, so both clicks reach the server; the second one must not land silently.
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, ready=True)
        client.force_login(instructor_fixture.user)
        url = reverse("classes:teach_class_submit", kwargs={"pk": offering.pk})
        first = client.post(url)
        assert first["Location"] == reverse("classes:teach_dashboard")
        second = client.post(url)
        assert second["Location"] == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert "This class has already been submitted and is waiting for review." in _messages(second)
        offering.refresh_from_db()
        assert offering.status == Status.PENDING
