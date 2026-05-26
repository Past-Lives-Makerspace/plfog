"""BDD specs for the admin email-students-from-class-detail feature."""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import InstructorMessage, InstructorMessageRecipient, Registration

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _email_outbox(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox = []
    yield
    mail.outbox = []


def describe_admin_class_email():
    def it_requires_admin_access(member_user, client):
        offering = ClassOfferingFactory()
        client.force_login(member_user)
        response = client.post(
            reverse("classes:admin_class_email", kwargs={"pk": offering.pk}),
            data={},
        )
        assert response.status_code == 403

    def it_rejects_get_requests(admin_user, client):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_email", kwargs={"pk": offering.pk}))
        assert response.status_code == 405

    def it_sends_and_redirects_on_success(admin_user, client):
        offering = ClassOfferingFactory()
        r1 = RegistrationFactory(class_offering=offering, email="s1@example.com", status=Registration.Status.CONFIRMED)
        r2 = RegistrationFactory(class_offering=offering, email="s2@example.com", status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_email", kwargs={"pk": offering.pk}),
            data={
                "subject": "Hello class",
                "body": "Welcome!",
                "registration_ids": [r1.pk, r2.pk],
                "bcc_self": "on",
            },
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.subject == "Hello class"
        assert "s1@example.com" in sent.bcc
        assert "s2@example.com" in sent.bcc
        message = InstructorMessage.objects.get()
        assert message.instructor is None
        assert message.recipient_count == 2
        assert InstructorMessageRecipient.objects.filter(message=message).count() == 2

    def it_excludes_cancelled_registrations_from_queryset(admin_user, client):
        offering = ClassOfferingFactory()
        active = RegistrationFactory(
            class_offering=offering, email="a@example.com", status=Registration.Status.CONFIRMED
        )
        cancelled = RegistrationFactory(
            class_offering=offering, email="c@example.com", status=Registration.Status.CANCELLED
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_email", kwargs={"pk": offering.pk}),
            data={
                "subject": "Hi",
                "body": "Test",
                "registration_ids": [active.pk, cancelled.pk],
            },
        )
        assert response.status_code == 302
        assert len(mail.outbox) == 0
        assert InstructorMessage.objects.count() == 0

    def it_shows_error_on_invalid_form(admin_user, client):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_email", kwargs={"pk": offering.pk}),
            data={"subject": "", "body": ""},
        )
        assert response.status_code == 302
        assert len(mail.outbox) == 0


def describe_admin_class_detail_students():
    def it_shows_registrations_on_class_detail(admin_user, client):
        offering = ClassOfferingFactory()
        RegistrationFactory(
            class_offering=offering, first_name="Alice", last_name="Smith", status=Registration.Status.CONFIRMED
        )
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
        assert response.status_code == 200
        assert b"Alice" in response.content
        assert b"Smith" in response.content
        assert b"Email selected students" in response.content

    def it_shows_empty_state_when_no_registrations(admin_user, client):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
        assert response.status_code == 200
        assert b"No registrations yet" in response.content
