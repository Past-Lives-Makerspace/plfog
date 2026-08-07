"""BDD specs for the marketing opt-in checkbox on account signup.

Covers the form itself, the end-to-end POST through allauth, and the rendered
template on both surfaces.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core.models import SiteConfiguration, UserProfile
from plfog.adapters import MarketingOptInSignupForm

pytestmark = pytest.mark.django_db

SUBSCRIBE_TARGET = "core.integrations.mailchimp.MailchimpClient.subscribe"


@pytest.fixture
def open_signup():
    config = SiteConfiguration.load()
    config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
    config.save()
    return config


@pytest.fixture
def site_with_mailchimp(open_signup):
    open_signup.mailchimp_api_key = "abc-us17"
    open_signup.mailchimp_list_id = "LISTID"
    open_signup.save()
    return open_signup


def describe_MarketingOptInSignupForm():
    def it_exposes_an_optional_unchecked_checkbox():
        form = MarketingOptInSignupForm()
        field = form.fields["wants_newsletter"]
        assert field.required is False
        assert field.initial is False

    def it_is_the_configured_allauth_signup_form(settings):
        assert settings.ACCOUNT_FORMS["signup"] == "plfog.adapters.MarketingOptInSignupForm"


def describe_signup_post():
    def it_subscribes_when_the_box_is_ticked(client, site_with_mailchimp):
        with patch(SUBSCRIBE_TARGET, return_value=True) as spy:
            client.post("/accounts/signup/", {"email": "yes@example.com", "wants_newsletter": "on"})

        spy.assert_called_once()
        assert spy.call_args.kwargs["email"] == "yes@example.com"
        assert spy.call_args.kwargs["tags"] == ["newsletter", "account-signup"]
        user = get_user_model().objects.get(email="yes@example.com")
        assert UserProfile.objects.get(user=user).subscribed_to_mailchimp_at is not None

    def it_does_not_subscribe_when_the_box_is_left_unticked(client, site_with_mailchimp):
        with patch(SUBSCRIBE_TARGET) as spy:
            client.post("/accounts/signup/", {"email": "no@example.com"})

        spy.assert_not_called()
        assert get_user_model().objects.filter(email="no@example.com").exists()

    def it_still_creates_the_account_when_mailchimp_rejects_the_push(client, site_with_mailchimp):
        with patch(SUBSCRIBE_TARGET, return_value=False):
            client.post("/accounts/signup/", {"email": "nope@example.com", "wants_newsletter": "on"})

        assert get_user_model().objects.filter(email="nope@example.com").exists()

    def it_still_creates_the_account_when_the_mailchimp_client_raises(client, site_with_mailchimp):
        # The push is best-effort and runs after the user is committed, so even a
        # pathological client that raises must not surface an error to the signup.
        with patch(SUBSCRIBE_TARGET, side_effect=RuntimeError("mailchimp exploded")):
            response = client.post("/accounts/signup/", {"email": "boom@example.com", "wants_newsletter": "on"})

        assert response.status_code < 500
        assert get_user_model().objects.filter(email="boom@example.com").exists()

    def it_creates_the_account_when_mailchimp_is_disabled(client, open_signup):
        with patch(SUBSCRIBE_TARGET) as spy:
            client.post("/accounts/signup/", {"email": "off@example.com", "wants_newsletter": "on"})

        spy.assert_not_called()
        assert get_user_model().objects.filter(email="off@example.com").exists()


def describe_signup_template():
    def it_renders_the_checkbox_on_the_members_surface(client, open_signup):
        content = client.get("/accounts/signup/").content.decode()
        assert 'name="wants_newsletter"' in content
        assert "Email me about new classes" in content

    def it_renders_the_checkbox_on_the_guest_surface(open_signup, settings):
        settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
        settings.PUBLIC_HOSTS = ["book.pastlives.space"]
        settings.MEMBER_HOST = "members.pastlives.space"

        content = Client(HTTP_HOST="book.pastlives.space").get("/accounts/signup/").content.decode()

        assert 'name="wants_newsletter"' in content
        assert "bk-auth-check" in content
