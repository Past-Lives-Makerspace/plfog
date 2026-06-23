"""BDD specs for admin_class_emails — welcome email authoring from the admin class workspace."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory

pytestmark = pytest.mark.django_db


def describe_admin_class_emails():
    def it_renders_for_an_admin(admin_user, client):
        offering = ClassOfferingFactory(slug="ace-render")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_emails", kwargs={"pk": offering.pk}))
        assert response.status_code == 200
        assert b"Welcome email" in response.content

    def it_saves_the_welcome_email(admin_user, client):
        offering = ClassOfferingFactory(slug="ace-save")
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_emails", kwargs={"pk": offering.pk}),
            {"welcome_email_enabled": "on", "welcome_email_subject": "Hi", "welcome_email_body": "Bring tools."},
        )
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.welcome_email_enabled is True

    def it_re_renders_with_errors_on_an_invalid_save(admin_user, client):
        offering = ClassOfferingFactory(slug="ace-invalid")
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_emails", kwargs={"pk": offering.pk}),
            {"welcome_email_enabled": "on", "welcome_email_subject": "", "welcome_email_body": ""},
        )
        assert response.status_code == 200
        offering.refresh_from_db()
        assert offering.welcome_email_enabled is False

    def it_sends_a_test_to_the_admin(admin_user, client, mailoutbox):
        offering = ClassOfferingFactory(slug="ace-test")
        client.force_login(admin_user)
        client.post(
            reverse("classes:admin_class_emails", kwargs={"pk": offering.pk}),
            {
                "welcome_email_enabled": "on",
                "welcome_email_subject": "Hi",
                "welcome_email_body": "Body",
                "send_test": "1",
            },
        )
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["admin@example.com"]

    def it_forbids_a_non_admin(member_user, client):
        offering = ClassOfferingFactory(slug="ace-forbid")
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_class_emails", kwargs={"pk": offering.pk}))
        assert response.status_code == 403
