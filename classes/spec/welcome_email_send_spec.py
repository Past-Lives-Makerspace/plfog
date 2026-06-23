"""BDD specs for the instructor welcome-email send helpers."""

from __future__ import annotations

import pytest

from classes.emails import send_class_welcome_email, send_class_welcome_email_test
from classes.factories import ClassOfferingFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


def _ready_offering(**kwargs):
    return ClassOfferingFactory(
        welcome_email_enabled=True,
        welcome_email_subject="Welcome!",
        welcome_email_body="Bring an apron.",
        **kwargs,
    )


def describe_send_class_welcome_email():
    def it_sends_when_the_welcome_email_is_ready(mailoutbox):
        offering = _ready_offering(slug="we-ready")
        reg = RegistrationFactory(class_offering=offering, first_name="Ada", email="new@example.com")
        send_class_welcome_email(reg)
        assert len(mailoutbox) == 1
        assert mailoutbox[0].subject == "Welcome!"
        assert mailoutbox[0].to == ["new@example.com"]
        assert "Bring an apron." in mailoutbox[0].body

    def it_does_nothing_when_the_welcome_email_is_disabled(mailoutbox):
        offering = ClassOfferingFactory(slug="we-off", welcome_email_subject="Hi", welcome_email_body="x")
        reg = RegistrationFactory(class_offering=offering)
        send_class_welcome_email(reg)
        assert mailoutbox == []

    def it_does_nothing_when_enabled_but_empty(mailoutbox):
        offering = ClassOfferingFactory(slug="we-empty", welcome_email_enabled=True)
        reg = RegistrationFactory(class_offering=offering)
        send_class_welcome_email(reg)
        assert mailoutbox == []


def describe_send_class_welcome_email_test():
    def it_sends_a_test_render_to_the_given_address(mailoutbox):
        offering = _ready_offering(slug="we-test")
        send_class_welcome_email_test(offering, "me@example.com")
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["me@example.com"]
        assert "Test" in mailoutbox[0].subject
        assert "Bring an apron." in mailoutbox[0].body
