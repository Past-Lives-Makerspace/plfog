"""BDD specs for beta feedback form and view."""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import Client

from django.contrib.auth.models import User

from hub.forms import BetaFeedbackForm


def describe_BetaFeedbackForm():
    def it_accepts_valid_data():
        form = BetaFeedbackForm({"category": "bug", "subject": "Broken page", "message": "Something is wrong"})

        assert form.is_valid()

    def it_requires_category():
        form = BetaFeedbackForm({"category": "", "subject": "Test", "message": "Test"})

        assert not form.is_valid()
        assert "category" in form.errors

    def it_requires_subject():
        form = BetaFeedbackForm({"category": "bug", "subject": "", "message": "Test"})

        assert not form.is_valid()
        assert "subject" in form.errors

    def it_requires_message():
        form = BetaFeedbackForm({"category": "bug", "subject": "Test", "message": ""})

        assert not form.is_valid()
        assert "message" in form.errors

    def it_rejects_invalid_category():
        form = BetaFeedbackForm({"category": "invalid", "subject": "Test", "message": "Test"})

        assert not form.is_valid()
        assert "category" in form.errors

    def it_accepts_all_valid_categories():
        for value, _label in BetaFeedbackForm.CATEGORY_CHOICES:
            form = BetaFeedbackForm({"category": value, "subject": "Test", "message": "Test"})
            assert form.is_valid(), f"Category '{value}' should be valid"


@pytest.mark.django_db
def describe_beta_feedback_view():
    def it_requires_login(client: Client):
        response = client.get("/feedback/")

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_renders_feedback_form(client: Client):
        User.objects.create_user(username="feedbacker", password="pass")
        client.login(username="feedbacker", password="pass")

        response = client.get("/feedback/")

        assert response.status_code == 200
        assert isinstance(response.context["form"], BetaFeedbackForm)

    def it_sends_email_on_valid_post(client: Client):
        User.objects.create_user(username="reporter", password="pass", email="reporter@example.com")
        client.login(username="reporter", password="pass")

        response = client.post(
            "/feedback/",
            {"category": "bug", "subject": "Button broken", "message": "The submit button does not work"},
        )

        assert response.status_code == 302
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert "[Bug Report]" in sent.subject
        assert "Button broken" in sent.subject
        assert "reporter@example.com" in sent.body
        assert "The submit button does not work" in sent.body

        # Decision 8: the send is now audited through the choke-point.
        from core.models import TransactionalEmailLog

        log = TransactionalEmailLog.objects.get()
        assert log.trigger_kind == "hub.beta_feedback"
        assert log.status == TransactionalEmailLog.Status.SENT

    def it_shows_success_message_on_valid_post(client: Client):
        User.objects.create_user(username="msguser", password="pass")
        client.login(username="msguser", password="pass")

        response = client.post(
            "/feedback/",
            {"category": "feature", "subject": "Add dark mode", "message": "Would be nice"},
            follow=True,
        )

        assert response.status_code == 200
        messages_list = list(response.context["messages"])
        assert len(messages_list) == 1
        assert "feedback" in str(messages_list[0]).lower()

    def it_re_renders_form_on_invalid_post(client: Client):
        User.objects.create_user(username="badpost", password="pass")
        client.login(username="badpost", password="pass")

        response = client.post("/feedback/", {"category": "bug", "subject": "", "message": ""})

        assert response.status_code == 200
        assert response.context["form"].errors
        assert len(mail.outbox) == 0
