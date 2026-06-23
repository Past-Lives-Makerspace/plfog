"""BDD specs for TeachWelcomeEmailForm."""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory
from classes.forms import TeachWelcomeEmailForm

pytestmark = pytest.mark.django_db


def describe_TeachWelcomeEmailForm():
    def it_saves_content_and_stamps_updated_at():
        offering = ClassOfferingFactory(slug="wf-save")
        form = TeachWelcomeEmailForm(
            {"welcome_email_enabled": True, "welcome_email_subject": "Hi", "welcome_email_body": "Bring tools."},
            instance=offering,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        saved.refresh_from_db()
        assert saved.welcome_email_enabled is True
        assert saved.welcome_email_subject == "Hi"
        assert saved.welcome_email_updated_at is not None

    def it_rejects_enabling_without_a_subject():
        offering = ClassOfferingFactory(slug="wf-nosub")
        form = TeachWelcomeEmailForm(
            {"welcome_email_enabled": True, "welcome_email_subject": "", "welcome_email_body": "Body"},
            instance=offering,
        )
        assert not form.is_valid()
        assert "welcome_email_subject" in form.errors

    def it_rejects_enabling_without_a_body():
        offering = ClassOfferingFactory(slug="wf-nobody")
        form = TeachWelcomeEmailForm(
            {"welcome_email_enabled": True, "welcome_email_subject": "Hi", "welcome_email_body": ""},
            instance=offering,
        )
        assert not form.is_valid()
        assert "welcome_email_body" in form.errors

    def it_allows_saving_disabled_with_empty_content():
        offering = ClassOfferingFactory(slug="wf-off")
        form = TeachWelcomeEmailForm(
            {"welcome_email_enabled": False, "welcome_email_subject": "", "welcome_email_body": ""},
            instance=offering,
        )
        assert form.is_valid(), form.errors
