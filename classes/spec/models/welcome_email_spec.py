"""BDD specs for ClassOffering.welcome_email_ready."""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory

pytestmark = pytest.mark.django_db


def describe_welcome_email_ready():
    def it_is_true_when_enabled_with_subject_and_body():
        offering = ClassOfferingFactory(
            welcome_email_enabled=True, welcome_email_subject="Hi", welcome_email_body="Bring an apron."
        )
        assert offering.welcome_email_ready is True

    def it_is_false_when_disabled():
        offering = ClassOfferingFactory(
            welcome_email_enabled=False, welcome_email_subject="Hi", welcome_email_body="Bring an apron."
        )
        assert offering.welcome_email_ready is False

    def it_is_false_when_the_subject_is_blank():
        offering = ClassOfferingFactory(
            welcome_email_enabled=True, welcome_email_subject="   ", welcome_email_body="Bring an apron."
        )
        assert offering.welcome_email_ready is False

    def it_is_false_when_the_body_is_blank():
        offering = ClassOfferingFactory(welcome_email_enabled=True, welcome_email_subject="Hi", welcome_email_body="")
        assert offering.welcome_email_ready is False
