"""Characterization specs for the member-facing class emails migrated onto the event spine.

Each of these senders now emits exactly ONE event that fans out the preserved rich email
(to ``registration.email`` via ``email_to``) AND a single in-app row (to the registrant's
linked user). These tests pin: same recipient, equivalent content, exactly one email, and
exactly one bell row — proving the old dedicated-send + model/task ``dispatch`` collapse to
one path with no double-send.
"""

from __future__ import annotations

import pytest
from django.core import mail

from classes.emails import (
    send_reminder_email,
    send_waitlist_joined_confirmation,
    send_waitlist_spot_opened,
)
from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration
from core.models import Notification

pytestmark = pytest.mark.django_db


def _linked_registration(*, status: str, email: str = "buyer@example.com"):
    """A registration whose member is a linked, emailed user (so in-app can deliver)."""
    member_user = UserFactory(email="member@example.com")
    member = member_user.member  # type: ignore[attr-defined]
    offering = ClassOfferingFactory(
        title="Pottery Wheel", instructor=InstructorFactory(user=UserFactory()), status=ClassOffering.Status.PUBLISHED
    )
    reg = RegistrationFactory(class_offering=offering, member=member, email=email, status=status)
    Notification.objects.all().delete()
    mail.outbox.clear()
    return reg, member_user, offering


def describe_send_waitlist_joined_confirmation():
    def it_sends_one_email_to_the_registrant_and_one_in_app_row():
        reg, member_user, offering = _linked_registration(status=Registration.Status.WAITLISTED)

        send_waitlist_joined_confirmation(reg)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["buyer@example.com"]
        assert offering.title in mail.outbox[0].subject
        assert "waitlist" in mail.outbox[0].subject.lower()
        # Exactly one "Added to the waitlist" bell row for the registrant's user.
        rows = Notification.objects.filter(trigger="waitlist_confirmed", user=member_user)
        assert rows.count() == 1
        assert rows.first().title == "Added to the waitlist"

    def it_emails_a_guest_with_no_in_app_row():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        reg = RegistrationFactory(
            class_offering=offering, email="guest@example.com", status=Registration.Status.WAITLISTED
        )
        assert reg.member is None
        Notification.objects.all().delete()
        mail.outbox.clear()

        send_waitlist_joined_confirmation(reg)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["guest@example.com"]
        assert Notification.objects.filter(trigger="waitlist_confirmed").count() == 0


def describe_send_waitlist_spot_opened():
    def it_sends_one_claim_email_and_one_in_app_row():
        reg, member_user, offering = _linked_registration(status=Registration.Status.WAITLISTED)

        send_waitlist_spot_opened(reg)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["buyer@example.com"]
        assert offering.title in mail.outbox[0].subject
        rows = Notification.objects.filter(trigger="waitlist_spot_available", user=member_user)
        assert rows.count() == 1
        assert rows.first().title == "A waitlist spot opened up"


def describe_send_reminder_email():
    def it_sends_one_reminder_email_and_one_in_app_row():
        reg, member_user, offering = _linked_registration(status=Registration.Status.CONFIRMED)
        session = ClassSessionFactory(class_offering=offering)

        send_reminder_email(reg, session)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["buyer@example.com"]
        assert offering.title in mail.outbox[0].subject
        rows = Notification.objects.filter(trigger="class_reminder", user=member_user)
        assert rows.count() == 1
        assert rows.first().title == "Class reminder"

    def it_dedupes_a_re_emitted_reminder_for_the_same_session():
        """The per-(registration, session) dedup period sends the reminder once even if
        the sender is invoked twice for the same session (e.g. an overlapping cron tick)."""
        reg, member_user, offering = _linked_registration(status=Registration.Status.CONFIRMED)
        session = ClassSessionFactory(class_offering=offering)

        send_reminder_email(reg, session)
        send_reminder_email(reg, session)

        assert len(mail.outbox) == 1
        assert Notification.objects.filter(trigger="class_reminder", user=member_user).count() == 1
