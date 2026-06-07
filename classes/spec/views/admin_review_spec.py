"""BDD specs for the admin class review view."""

from __future__ import annotations

from django.urls import reverse

from classes.factories import ClassOfferingFactory
from classes.models import ClassApproval, ClassOffering


def describe_admin_class_review():
    def it_returns_200_for_admin(admin_user, client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        offering.submit_for_review()
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
        assert response.status_code == 200

    def it_returns_403_for_non_admin(member_user, client, db):
        offering = ClassOfferingFactory()
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
        assert response.status_code == 403

    def it_redirects_anonymous_to_login(client, db):
        offering = ClassOfferingFactory()
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        assert "/login" in response["Location"] or "/accounts" in response["Location"]

    def it_returns_404_for_unknown_offering(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_review", kwargs={"pk": 99999}))
        assert response.status_code == 404

    def it_records_approved_decision_on_post(admin_user, client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        assert response.status_code == 302
        row.refresh_from_db()
        assert row.decision == ClassApproval.Decision.APPROVED

    def it_records_changes_requested_on_post(admin_user, client, db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": ClassApproval.Decision.CHANGES_REQUESTED, "notes": "Add more detail."},
        )
        assert response.status_code == 302
        row.refresh_from_db()
        assert row.decision == ClassApproval.Decision.CHANGES_REQUESTED
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT
