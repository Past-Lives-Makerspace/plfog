"""BDD specs for the tokenized (unauthenticated) class review view."""

from __future__ import annotations

from django.urls import reverse

from classes.factories import ClassOfferingFactory
from classes.models import ClassApproval, ClassOffering


def describe_class_review():
    def it_returns_200_with_valid_token(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        response = client.get(reverse("classes:class_review", kwargs={"token": row.token}))
        assert response.status_code == 200

    def it_returns_404_with_invalid_token(client, db):
        response = client.get(reverse("classes:class_review", kwargs={"token": "not-a-real-token"}))
        assert response.status_code == 404

    def it_does_not_require_login(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        # Unauthenticated client should reach the page
        response = client.get(reverse("classes:class_review", kwargs={"token": row.token}))
        assert response.status_code == 200

    def it_records_approved_decision_on_post(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        response = client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        assert response.status_code == 302
        row.refresh_from_db()
        assert row.decision == ClassApproval.Decision.APPROVED
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_records_denial_on_post(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        response = client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.DENIED, "notes": "Not suitable."},
        )
        assert response.status_code == 302
        row.refresh_from_db()
        assert row.decision == ClassApproval.Decision.DENIED
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT

    def it_ignores_post_when_decision_already_recorded(client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        # Record first decision
        client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        # Second POST: decision guard blocks it; view re-renders (200) rather than redirecting
        response = client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.DENIED, "notes": "Changed my mind"},
        )
        assert response.status_code == 200
        row.refresh_from_db()
        assert row.decision == ClassApproval.Decision.APPROVED
