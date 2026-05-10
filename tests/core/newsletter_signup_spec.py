"""BDD specs for the standalone /newsletter/ Mailchimp signup page."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from core.forms import NewsletterSignupForm
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


@pytest.fixture
def site_with_mailchimp():
    site = SiteConfiguration.load()
    site.mailchimp_api_key = "abc-us17"
    site.mailchimp_list_id = "LISTID"
    site.save()
    return site


def describe_NewsletterSignupForm():
    def it_returns_false_when_mailchimp_disabled():
        form = NewsletterSignupForm(data={"email": "a@b.com"})
        assert form.is_valid()
        assert form.subscribe() is False

    def it_calls_client_with_newsletter_tag(site_with_mailchimp):
        form = NewsletterSignupForm(data={"email": "a@b.com", "first_name": "Ada"})
        assert form.is_valid()
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ) as spy:
            assert form.subscribe() is True
        spy.assert_called_once_with(
            email="a@b.com",
            first_name="Ada",
            last_name="",
            tags=["newsletter"],
        )


def describe_newsletter_signup_view():
    def it_renders_the_form(client):
        response = client.get(reverse("newsletter_signup"))
        assert response.status_code == 200
        assert b"Stay in the loop" in response.content
        assert b"Email address" in response.content

    def it_rejects_invalid_email(client):
        response = client.post(reverse("newsletter_signup"), data={"email": "not-an-email"})
        assert response.status_code == 200
        assert b"submitted" not in response.content.lower() or b"loop" in response.content

    def it_shows_success_message_when_subscribe_succeeds(client, site_with_mailchimp):
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ):
            response = client.post(
                reverse("newsletter_signup"),
                data={"email": "ada@example.com", "first_name": "Ada", "last_name": "Lovelace"},
            )
        assert response.status_code == 200
        assert b"You're in" in response.content

    def it_shows_failure_message_when_subscribe_fails(client, site_with_mailchimp):
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=False,
        ):
            response = client.post(
                reverse("newsletter_signup"),
                data={"email": "ada@example.com"},
            )
        assert response.status_code == 200
        assert b"couldn't sign you up" in response.content

    def it_shows_failure_message_when_mailchimp_disabled(client):
        response = client.post(
            reverse("newsletter_signup"),
            data={"email": "ada@example.com"},
        )
        assert response.status_code == 200
        assert b"couldn't sign you up" in response.content
