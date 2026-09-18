"""BDD specs for registration notification emails (instructor + admin)."""

from __future__ import annotations

from django.core import mail

from classes.emails import emit_instructor_new_registration, send_admin_registration_notification
from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory
from classes.models import ClassOffering
from core.models import Notification


def describe_emit_instructor_new_registration():
    def it_emails_the_instructor_with_registration_details(db):
        user = UserFactory(email="teach@example.com")
        instructor = InstructorFactory(user=user, full_legal_name="Ms. Paint")
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

        emit_instructor_new_registration(registration)

        # Exactly one notice email — no second generic email from a separate dispatch.
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == ["teach@example.com"]
        assert "Sam Park" in msg.subject
        assert "Watercolors 101" in msg.subject
        assert "$50.00" in msg.body
        assert "1/8" in msg.body
        # And exactly one in-app row for the instructor.
        assert Notification.objects.filter(trigger="instructor_new_registration", user=user).count() == 1

    def it_skips_when_instructor_has_no_email(db):
        user = UserFactory(email="")
        instructor = InstructorFactory(user=user)
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        registration = RegistrationFactory(class_offering=offering)

        emit_instructor_new_registration(registration)

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

        # The spine sends one email per admin address (deduped + audited per recipient);
        # the recipient SET is identical to the old single multi-To send.
        assert len(mail.outbox) == 2
        assert {addr for m in mail.outbox for addr in m.to} == {"admin1@example.com", "admin2@example.com"}
        msg = mail.outbox[0]
        assert "Alex Doe" in msg.subject
        assert "Pottery Basics" in msg.subject
        assert "$80.00" in msg.body
        assert offering.instructor.display_name in msg.body
        assert "Capacity: 1/6" in msg.body

    def it_counts_capacity_the_way_every_other_surface_does(db, settings):
        """The admin email goes out one line after the instructor's, about the same signup.

        Counting every row ever written had the two disagree by four in the same instant.
        """
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
        offering = ClassOfferingFactory(capacity=6, status=ClassOffering.Status.PUBLISHED)
        for _ in range(4):
            RegistrationFactory(class_offering=offering, status="cancelled")
        newest = RegistrationFactory(class_offering=offering, status="confirmed")

        send_admin_registration_notification(newest)

        body = "\n".join(m.body for m in mail.outbox)
        assert "Capacity: 1/6" in body
        assert "5/6" not in body

    def it_agrees_with_the_instructor_email_about_the_same_signup(db, settings):
        """Both go out from the same two lines (classes/views.py and webhook_handlers.py).

        The admin half counted every row ever written, so on a class with four cancelled
        signups the instructor was told 1/6 and the admins 5/6 about one registration.
        """
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
        user = UserFactory(email="teach@example.com")
        instructor = InstructorFactory(user=user)
        offering = ClassOfferingFactory(instructor=instructor, capacity=6, status=ClassOffering.Status.PUBLISHED)
        for _ in range(4):
            RegistrationFactory(class_offering=offering, status="cancelled")
        for _ in range(2):
            RegistrationFactory(class_offering=offering, status="waitlisted")
        newest = RegistrationFactory(class_offering=offering, status="confirmed")

        emit_instructor_new_registration(newest)
        send_admin_registration_notification(newest)

        bodies = [m.body for m in mail.outbox]
        assert len(bodies) == 2
        assert all("1/6" in body for body in bodies)

    def it_does_not_count_the_waitlist_against_capacity(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
        offering = ClassOfferingFactory(capacity=9, status=ClassOffering.Status.PUBLISHED)
        for _ in range(2):
            RegistrationFactory(class_offering=offering, status="confirmed")
        for _ in range(5):
            RegistrationFactory(class_offering=offering, status="waitlisted")
        newest = RegistrationFactory(class_offering=offering, status="pending")

        send_admin_registration_notification(newest)

        body = "\n".join(m.body for m in mail.outbox)
        assert "Capacity: 3/9" in body
        assert "8/9" not in body

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
