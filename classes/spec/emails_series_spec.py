"""BDD specs for series-aware registration confirmation emails."""

from __future__ import annotations

import pytest
from django.core import mail

from classes.emails import send_registration_confirmation
from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    SeriesClassOfferingFactory,
)
from classes.models import ClassOffering, Registration

pytestmark = pytest.mark.django_db


def describe_send_registration_confirmation_for_a_series():
    def it_lists_every_session_date_and_a_series_note():
        offering = SeriesClassOfferingFactory(
            title="Blacksmithing 101",
            instructor=InstructorFactory(full_legal_name="Glen"),
            status=ClassOffering.Status.PUBLISHED,
            session_count=3,
        )
        registration = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=15000,
            email="buyer@example.com",
        )

        send_registration_confirmation(registration)

        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert "all 3 sessions" in body
        # Every scheduled date appears in the schedule list.
        sessions = list(offering.sessions.order_by("starts_at"))
        assert len(sessions) == 3
        for session in sessions:
            assert session.starts_at.strftime("%B").lstrip("0") in body or str(session.starts_at.day) in body


def describe_send_registration_confirmation_for_a_single():
    def it_omits_the_series_note():
        offering = ClassOfferingFactory(
            title="One-Off Welding",
            instructor=InstructorFactory(full_legal_name="Pat"),
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(class_offering=offering)
        registration = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=8000,
            email="buyer@example.com",
        )

        send_registration_confirmation(registration)

        assert len(mail.outbox) == 1
        assert "series package" not in mail.outbox[0].body.lower()
