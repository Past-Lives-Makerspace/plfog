"""BDD specs for the instructor manual email composer (Gap 5)."""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import InstructorMessage, InstructorMessageRecipient

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _email_outbox(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox = []
    yield
    mail.outbox = []


@pytest.fixture
def instructor():
    user = UserFactory(username="teacher@example.com", email="teacher@example.com")
    return InstructorFactory(user=user, display_name="Teacher T", slug="teacher-t")


@pytest.fixture
def other_instructor():
    user = UserFactory(username="other@example.com", email="other@example.com")
    return InstructorFactory(user=user, display_name="Other", slug="other")


def describe_instructor_email_composer():
    def it_requires_an_active_instructor(member_user, client):
        client.force_login(member_user)
        response = client.post(reverse("classes:instructor_registrations_email"), data={})
        assert response.status_code == 403

    def it_redirects_back_with_error_when_form_invalid(instructor, client):
        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor)
        reg = RegistrationFactory(class_offering=offering)
        response = client.post(
            reverse("classes:instructor_registrations_email"),
            data={"subject": "", "body": "", "registration_ids": [reg.pk]},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:instructor_registrations")
        assert len(mail.outbox) == 0

    def it_rejects_recipients_outside_my_classes(instructor, other_instructor, client):
        client.force_login(instructor.user)
        my_offering = ClassOfferingFactory(instructor=instructor)
        their_offering = ClassOfferingFactory(instructor=other_instructor)
        mine = RegistrationFactory(class_offering=my_offering)
        theirs = RegistrationFactory(class_offering=their_offering)
        client.post(
            reverse("classes:instructor_registrations_email"),
            data={
                "subject": "Hi",
                "body": "Reminder.",
                "registration_ids": [mine.pk, theirs.pk],  # theirs must be rejected
                "bcc_self": "on",
            },
        )
        # Form-level error: theirs isn't in the queryset; the whole form fails.
        assert len(mail.outbox) == 0
        assert InstructorMessage.objects.count() == 0

    def it_sends_with_bcc_and_records_audit_rows(instructor, client):
        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor)
        r1 = RegistrationFactory(class_offering=offering, email="alice@example.com")
        r2 = RegistrationFactory(class_offering=offering, email="bob@example.com")
        response = client.post(
            reverse("classes:instructor_registrations_email"),
            data={
                "subject": "Class reminder",
                "body": "See you tomorrow at 10am.",
                "registration_ids": [r1.pk, r2.pk],
                "bcc_self": "on",
            },
        )
        assert response.status_code == 302
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.subject == "Class reminder"
        assert sent.body == "See you tomorrow at 10am."
        assert "alice@example.com" in sent.bcc
        assert "bob@example.com" in sent.bcc
        assert "teacher@example.com" in sent.bcc  # bcc_self was on
        message = InstructorMessage.objects.get()
        assert message.recipient_count == 2
        assert message.instructor == instructor
        assert InstructorMessageRecipient.objects.filter(message=message).count() == 2

    def it_omits_self_from_bcc_when_unchecked(instructor, client):
        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor)
        r1 = RegistrationFactory(class_offering=offering, email="alice@example.com")
        client.post(
            reverse("classes:instructor_registrations_email"),
            data={
                "subject": "Hi",
                "body": "Yo",
                "registration_ids": [r1.pk],
                # bcc_self omitted from form post → unchecked
            },
        )
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert "alice@example.com" in sent.bcc
        assert "teacher@example.com" not in sent.bcc
