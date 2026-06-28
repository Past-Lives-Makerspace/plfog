"""Tests for inline notification dispatch at class lifecycle + registration events."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassApproval, ClassOffering, ClassSettings, Registration
from classes.tasks import send_due_class_reminders
from classes.webhook_handlers import handle_checkout_session_completed
from core.models import Notification

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_member_user() -> User:
    """Create an ACTIVE, *activated* member User.

    The ensure_user_has_member signal auto-creates an ACTIVE Member; ``last_login`` is
    set so the user is "activated" and qualifies for broadcast dispatches (the spine
    never broadcasts to a member who has never signed in).
    """
    return UserFactory(last_login=timezone.now())


def _publish_offering(offering: ClassOffering) -> None:
    """Drive an offering through DRAFT → PENDING → PUBLISHED via the approval pathway."""
    offering.status = ClassOffering.Status.PENDING
    offering.save(update_fields=["status", "updated_at"])
    approval = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
    admin_user = UserFactory()
    approval.decide(ClassApproval.Decision.APPROVED, user=admin_user)
    offering.refresh_from_db()


# ---------------------------------------------------------------------------
# class_published — broadcast to all active members
# ---------------------------------------------------------------------------


def describe_class_published_notification():
    def it_dispatches_to_active_members_when_published(db):
        recipient = _active_member_user()
        instructor = InstructorFactory(user=UserFactory())
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, instructor=instructor)
        Notification.objects.all().delete()

        _publish_offering(offering)

        assert Notification.objects.filter(
            trigger="class_published",
            user=recipient,
        ).exists()


# ---------------------------------------------------------------------------
# instructor_class_approved — notify instructor only when it's a partial approve
# ---------------------------------------------------------------------------


def describe_instructor_class_approved_notification():
    def it_notifies_instructor_on_approval(db):
        from classes.emails import send_class_review_decision

        instructor_user = UserFactory()
        instructor = InstructorFactory(user=instructor_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING, instructor=instructor)
        admin_user = UserFactory()
        approval = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        approval.decide(ClassApproval.Decision.APPROVED, user=admin_user)
        offering.refresh_from_db()
        approval.refresh_from_db()
        Notification.objects.all().delete()

        # The instructor's "approved" bell row now fans out from the single
        # ``instructor_class_approved`` event emitted by the decision email (the view
        # calls this right after decide()), not from a separate model dispatch.
        send_class_review_decision(offering, approval)

        assert Notification.objects.filter(
            trigger="instructor_class_approved",
            user=instructor_user,
        ).exists()


# ---------------------------------------------------------------------------
# instructor_changes_requested
# ---------------------------------------------------------------------------


def describe_instructor_changes_requested_notification():
    def it_notifies_instructor_when_changes_requested(db):
        from classes.emails import send_class_review_decision

        instructor_user = UserFactory()
        instructor = InstructorFactory(user=instructor_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING, instructor=instructor)
        admin_user = UserFactory()
        approval = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        approval.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=admin_user, notes="please fix the desc")
        offering.refresh_from_db()
        approval.refresh_from_db()
        Notification.objects.all().delete()

        # The "changes requested" bell row now fans out from the single
        # ``instructor_changes_requested`` event emitted by the decision email.
        send_class_review_decision(offering, approval)

        assert Notification.objects.filter(
            trigger="instructor_changes_requested",
            user=instructor_user,
        ).exists()


# ---------------------------------------------------------------------------
# registration_confirmed
# ---------------------------------------------------------------------------


def describe_registration_confirmed_notification():
    def it_notifies_member_when_registration_is_confirmed(db):
        from classes.emails import send_registration_confirmation

        member_user = UserFactory()
        # The ensure_user_has_member signal auto-creates an ACTIVE Member for member_user.
        member = member_user.member  # type: ignore[attr-defined]
        instructor = InstructorFactory(user=UserFactory())
        offering = ClassOfferingFactory(instructor=instructor)
        reg = RegistrationFactory(
            class_offering=offering,
            member=member,
            email=member_user.email,
            status=Registration.Status.CONFIRMED,
        )
        Notification.objects.all().delete()

        # The "Registration confirmed" bell row now fans out from the single
        # ``registration_confirmed`` event emitted by the confirmation email (called by
        # the view/webhook right after the CONFIRMED transition), not the model save.
        send_registration_confirmation(reg)

        assert Notification.objects.filter(
            trigger="registration_confirmed",
            user=member_user,
        ).exists()

    def it_sends_exactly_one_confirmation_email_to_an_opted_in_member(db, settings):
        """Double-send eliminated: the single ``registration_confirmed`` event sends the
        rich confirmation email once (via email_to) and posts exactly one in-app row, even
        for a member who opted into the confirmation email."""
        from django.core import mail

        from classes.emails import send_registration_confirmation
        from core.models import NotificationPreference

        member_user = UserFactory()
        member = member_user.member  # type: ignore[attr-defined]
        offering = ClassOfferingFactory(instructor=InstructorFactory(user=UserFactory()))
        reg = RegistrationFactory(
            class_offering=offering,
            member=member,
            email=member_user.email,
            status=Registration.Status.CONFIRMED,
        )
        # Opt the member into the confirmation email — the single event still produces
        # exactly one email (the rich shell via email_to), never a second generic one.
        NotificationPreference.objects.create(
            user=member_user, event_key="registration_confirmed", channel="email", enabled=True
        )
        Notification.objects.all().delete()
        mail.outbox.clear()

        send_registration_confirmation(reg)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [member_user.email]
        assert offering.title in mail.outbox[0].subject
        assert Notification.objects.filter(trigger="registration_confirmed", user=member_user).count() == 1


# ---------------------------------------------------------------------------
# waitlist_confirmed
# ---------------------------------------------------------------------------


def describe_waitlist_confirmed_notification():
    def it_notifies_member_when_added_to_waitlist(db):
        from classes.emails import send_waitlist_joined_confirmation

        member_user = UserFactory()
        # The ensure_user_has_member signal auto-creates an ACTIVE Member for member_user.
        member = member_user.member  # type: ignore[attr-defined]
        offering = ClassOfferingFactory()
        reg = RegistrationFactory(
            class_offering=offering,
            member=member,
            email=member_user.email,
            status=Registration.Status.WAITLISTED,
        )
        Notification.objects.all().delete()

        # The "Added to the waitlist" bell row now fans out from the single
        # ``waitlist_confirmed`` event emitted by the waitlist email (called by the
        # register view right after the WAITLISTED save), not the model save.
        send_waitlist_joined_confirmation(reg)

        assert Notification.objects.filter(
            trigger="waitlist_confirmed",
            user=member_user,
        ).exists()


# ---------------------------------------------------------------------------
# refund_issued
# ---------------------------------------------------------------------------


def describe_refund_issued_notification():
    def it_notifies_member_when_refund_is_issued(db):
        member_user = UserFactory()
        # The ensure_user_has_member signal auto-creates an ACTIVE Member for member_user.
        member = member_user.member  # type: ignore[attr-defined]
        offering = ClassOfferingFactory()
        reg = RegistrationFactory(
            class_offering=offering,
            member=member,
            email=member_user.email,
            status=Registration.Status.CONFIRMED,
        )
        Notification.objects.all().delete()

        reg.status = Registration.Status.REFUNDED
        reg.save()

        assert Notification.objects.filter(
            trigger="refund_issued",
            user=member_user,
        ).exists()


# ---------------------------------------------------------------------------
# waitlist_spot_available
# ---------------------------------------------------------------------------


def describe_waitlist_spot_available_notification():
    def it_notifies_waitlisted_member_when_spot_opens(db):
        member_user = UserFactory()
        # The ensure_user_has_member signal auto-creates an ACTIVE Member for member_user.
        member = member_user.member  # type: ignore[attr-defined]
        offering = ClassOfferingFactory(capacity=1)
        # Fill the one spot with a confirmed reg
        confirmed_reg = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
        )
        # Add waitlisted member
        RegistrationFactory(
            class_offering=offering,
            member=member,
            email=member_user.email,
            status=Registration.Status.WAITLISTED,
        )
        Notification.objects.all().delete()

        # Cancel the confirmed reg — should promote the waitlisted member
        confirmed_reg.cancel()

        assert Notification.objects.filter(
            trigger="waitlist_spot_available",
            user=member_user,
        ).exists()


# ---------------------------------------------------------------------------
# class_cancelled — broadcast to all active members
# ---------------------------------------------------------------------------


def describe_class_cancelled_notification():
    def it_dispatches_to_active_members_when_class_is_archived(db):
        recipient = _active_member_user()
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        Notification.objects.all().delete()

        offering.archive()

        assert Notification.objects.filter(
            trigger="class_cancelled",
            user=recipient,
        ).exists()


# ---------------------------------------------------------------------------
# instructor_new_registration — via wired dispatch inline after email call
# ---------------------------------------------------------------------------


def describe_instructor_new_registration_notification():
    def it_notifies_instructor_when_registration_confirmed_via_webhook(db):
        """handle_checkout_session_completed dispatches instructor_new_registration."""
        instructor_user = UserFactory()
        instructor = InstructorFactory(user=instructor_user)
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        reg = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.PENDING,
            stripe_session_id="cs_test_notif",
            amount_paid_cents=5000,
        )
        Notification.objects.all().delete()

        event = {
            "data": {
                "object": {
                    "id": "cs_test_notif",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_notif",
                    "amount_total": 5000,
                    "metadata": {
                        "kind": "class_registration",
                        "registration_id": str(reg.pk),
                    },
                }
            }
        }
        handle_checkout_session_completed(event)

        assert Notification.objects.filter(
            trigger="instructor_new_registration",
            user=instructor_user,
        ).exists()


# ---------------------------------------------------------------------------
# class_reminder — dispatched by send_due_class_reminders task
# ---------------------------------------------------------------------------


def describe_class_reminder_notification():
    def it_creates_a_notification_for_the_registrant(db, settings):
        settings.DEFAULT_FROM_EMAIL = "noreply@pastlives.space"
        cfg = ClassSettings.load()
        cfg.reminder_hours_before = 24
        cfg.save()

        member_user = UserFactory()
        # The ensure_user_has_member signal auto-creates an ACTIVE Member for member_user.
        member = member_user.member  # type: ignore[attr-defined]

        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(hours=24, minutes=1)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        RegistrationFactory(
            class_offering=offering,
            member=member,
            email=member_user.email,
            status=Registration.Status.CONFIRMED,
        )
        Notification.objects.all().delete()

        send_due_class_reminders(window_minutes=30)

        assert Notification.objects.filter(trigger="class_reminder", user=member_user).exists()
