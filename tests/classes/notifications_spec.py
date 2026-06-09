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
    """Create a User — the ensure_user_has_member signal auto-creates an ACTIVE Member.

    We don't need to create a Member manually; the signal handles it.
    The auto-created member has status=ACTIVE so it qualifies for broadcast dispatches.
    """
    return UserFactory()


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
        instructor_user = UserFactory()
        instructor = InstructorFactory(user=instructor_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING, instructor=instructor)
        Notification.objects.all().delete()

        admin_user = UserFactory()
        approval = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        # Use a guild category that ALSO requires a guild lead so approval alone doesn't publish
        # — simpler: test with a single-role offering so approved fires the published path too;
        #   the instructor_class_approved notification is always dispatched on any APPROVED decision.
        approval.decide(ClassApproval.Decision.APPROVED, user=admin_user)

        assert Notification.objects.filter(
            trigger="instructor_class_approved",
            user=instructor_user,
        ).exists()


# ---------------------------------------------------------------------------
# instructor_changes_requested
# ---------------------------------------------------------------------------


def describe_instructor_changes_requested_notification():
    def it_notifies_instructor_when_changes_requested(db):
        instructor_user = UserFactory()
        instructor = InstructorFactory(user=instructor_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING, instructor=instructor)
        Notification.objects.all().delete()

        admin_user = UserFactory()
        approval = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        approval.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=admin_user, notes="please fix the desc")

        assert Notification.objects.filter(
            trigger="instructor_changes_requested",
            user=instructor_user,
        ).exists()


# ---------------------------------------------------------------------------
# registration_confirmed
# ---------------------------------------------------------------------------


def describe_registration_confirmed_notification():
    def it_notifies_member_when_registration_is_confirmed(db):
        member_user = UserFactory()
        # The ensure_user_has_member signal auto-creates an ACTIVE Member for member_user.
        member = member_user.member  # type: ignore[attr-defined]
        instructor = InstructorFactory(user=UserFactory())
        offering = ClassOfferingFactory(instructor=instructor)
        reg = RegistrationFactory(
            class_offering=offering,
            member=member,
            email=member_user.email,
            status=Registration.Status.PENDING,
        )
        Notification.objects.all().delete()

        reg.status = Registration.Status.CONFIRMED
        reg.save()

        assert Notification.objects.filter(
            trigger="registration_confirmed",
            user=member_user,
        ).exists()


# ---------------------------------------------------------------------------
# waitlist_confirmed
# ---------------------------------------------------------------------------


def describe_waitlist_confirmed_notification():
    def it_notifies_member_when_added_to_waitlist(db):
        member_user = UserFactory()
        # The ensure_user_has_member signal auto-creates an ACTIVE Member for member_user.
        member = member_user.member  # type: ignore[attr-defined]
        offering = ClassOfferingFactory()
        # Creating a WAITLISTED registration via save() dispatches waitlist_confirmed
        Notification.objects.all().delete()

        RegistrationFactory(
            class_offering=offering,
            member=member,
            email=member_user.email,
            status=Registration.Status.WAITLISTED,
        )

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
