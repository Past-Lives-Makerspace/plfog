"""BDD specs for the account-signup -> Mailchimp bridge."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import SiteConfiguration, UserProfile
from core.services.mailchimp_account import derive_account_tags, subscribe_user

pytestmark = pytest.mark.django_db


def _user(email: str = "new@example.com", **kwargs):
    User = get_user_model()
    return User.objects.create_user(username=email, email=email, **kwargs)


@pytest.fixture
def site_with_mailchimp():
    site = SiteConfiguration.load()
    site.mailchimp_api_key = "abc-us17"
    site.mailchimp_list_id = "LISTID"
    site.save()
    return site


def describe_derive_account_tags():
    def it_tags_the_shared_newsletter_tag_and_the_signup_door():
        assert derive_account_tags(_user()) == ["newsletter", "account-signup"]


def describe_subscribe_user():
    def it_subscribes_and_stamps_the_profile(site_with_mailchimp):
        user = _user()
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe", return_value=True) as spy:
            assert subscribe_user(user) is True
        spy.assert_called_once()
        assert spy.call_args.kwargs["email"] == "new@example.com"
        assert spy.call_args.kwargs["tags"] == ["newsletter", "account-signup"]
        assert UserProfile.objects.get(user=user).subscribed_to_mailchimp_at is not None

    def it_passes_the_users_name_as_merge_fields(site_with_mailchimp):
        user = _user(first_name="Ada", last_name="Lovelace")
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe", return_value=True) as spy:
            subscribe_user(user)
        assert spy.call_args.kwargs["first_name"] == "Ada"
        assert spy.call_args.kwargs["last_name"] == "Lovelace"

    def it_reuses_an_existing_profile_rather_than_creating_a_second(site_with_mailchimp):
        user = _user()
        profile = UserProfile.objects.create(user=user, pronouns="they/them")
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe", return_value=True):
            subscribe_user(user)
        profile.refresh_from_db()
        assert profile.subscribed_to_mailchimp_at is not None
        assert profile.pronouns == "they/them"
        assert UserProfile.objects.filter(user=user).count() == 1

    def it_does_nothing_when_the_user_has_no_email(site_with_mailchimp):
        User = get_user_model()
        user = User.objects.create_user(username="noemail", email="")
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            assert subscribe_user(user) is False
        spy.assert_not_called()

    def it_does_nothing_when_already_subscribed(site_with_mailchimp):
        user = _user()
        UserProfile.objects.create(user=user, subscribed_to_mailchimp_at=timezone.now())
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            assert subscribe_user(user) is False
        spy.assert_not_called()

    def it_does_nothing_when_mailchimp_is_disabled():
        # No site config and no env fallback -- client.enabled is False.
        user = _user()
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            assert subscribe_user(user) is False
        spy.assert_not_called()

    def it_does_not_stamp_the_profile_when_mailchimp_rejects_the_push(site_with_mailchimp):
        user = _user()
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe", return_value=False):
            assert subscribe_user(user) is False
        assert not UserProfile.objects.filter(user=user, subscribed_to_mailchimp_at__isnull=False).exists()
