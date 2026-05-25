"""BDD specs for registration notification emails (instructor + admin)."""

from __future__ import annotations

from django.core import mail

from classes.emails import send_admin_registration_notification, send_instructor_registration_notification
from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory
from classes.models import ClassOffering


def describe_send_instructor_registration_notification():
    def it_emails_the_instructor_with_registration_details(db):
        user = UserFactory(email="teach@example.com")
        instructor = InstructorFactory(user=user, display_name="Ms. Paint")
        offering = ClassOfferingFactory(
            instructor=instructor, title="Watercolors 101", capacity=8, status=ClassOffering.Status.PUBLISHED
        )
        registration = RegistrationFactory(
            class_offering=offering,
            first_name="Sam",
            last_name="Park",
            email="sam@example.com",
            amount_paid_cents=5000,
            status="confirmed",
        )

        send_instructor_registration_notification(registration)

        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == ["teach@example.com"]
        assert "Sam Park" in msg.subject
        assert "Watercolors 101" in msg.subject
        assert "$50.00" in msg.body
        assert "1/8" in msg.body

    def it_skips_when_instructor_has_no_email(db):
        user = UserFactory(email="")
        instructor = InstructorFactory(user=user)
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        registration = RegistrationFactory(class_offering=offering)

        send_instructor_registration_notification(registration)

        assert len(mail.outbox) == 0


def describe_send_admin_registration_notification():
    def it_emails_configured_admins(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin1@example.com, admin2@example.com"
        offering = ClassOfferingFactory(title="Pottery Basics", capacity=6, status=ClassOffering.Status.PUBLISHED)
        registration = RegistrationFactory(
            class_offering=offering,
            first_name="Alex",
            last_name="Doe",
            email="alex@example.com",
            amount_paid_cents=8000,
            status="confirmed",
        )

        send_admin_registration_notification(registration)

        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == ["admin1@example.com", "admin2@example.com"]
        assert "Alex Doe" in msg.subject
        assert "Pottery Basics" in msg.subject
        assert "$80.00" in msg.body
        assert offering.instructor.display_name in msg.body

    def it_skips_when_no_admin_emails_configured(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        registration = RegistrationFactory()

        send_admin_registration_notification(registration)

        assert len(mail.outbox) == 0

    def it_skips_when_setting_is_only_whitespace(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "  ,  , "
        registration = RegistrationFactory()

        send_admin_registration_notification(registration)

        assert len(mail.outbox) == 0
