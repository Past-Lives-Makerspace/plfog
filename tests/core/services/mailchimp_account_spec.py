"""BDD specs for core.services.mailchimp_account.subscribe_user."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from core.models import SiteConfiguration, UserProfile
from core.services.mailchimp_account import derive_account_tags, subscribe_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def mailchimp_configured():
    site = SiteConfiguration.load()
    site.mailchimp_api_key = "abc-us17"
    site.mailchimp_list_id = "LISTID"
    site.save()
    return site


def _user_with_profile(**profile_kwargs):
    User = get_user_model()
    user = User.objects.create_user(
        username=profile_kwargs.pop("username", "ada@example.com"),
        email=profile_kwargs.pop("email", "ada@example.com"),
        password="x",
        first_name=profile_kwargs.pop("first_name", "Ada"),
        last_name=profile_kwargs.pop("last_name", "Lovelace"),
    )
    UserProfile.objects.create(user=user, **profile_kwargs)
    return user


def describe_derive_account_tags():
    def it_always_includes_account_signup():
        user = _user_with_profile()
        assert "account-signup" in derive_account_tags(user.profile)

    def it_includes_persona_when_set():
        user = _user_with_profile(
            first_attendance_status=UserProfile.FirstAttendance.FIRST_TIME,
        )
        assert "persona-first_time" in derive_account_tags(user.profile)

    def it_includes_referral_when_set():
        user = _user_with_profile(referral_source=UserProfile.Referral.INSTAGRAM)
        assert "referral-instagram" in derive_account_tags(user.profile)

    def it_includes_one_interest_tag_per_slug():
        user = _user_with_profile(interest_category_slugs=["wood", "glass"])
        tags = derive_account_tags(user.profile)
        assert "interest-wood" in tags
        assert "interest-glass" in tags


def describe_subscribe_user():
    def it_no_ops_when_user_has_no_profile(mailchimp_configured):
        User = get_user_model()
        user = User.objects.create_user(username="bob@example.com", email="bob@example.com", password="x")
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            subscribe_user(user)
        spy.assert_not_called()

    def it_no_ops_when_mailchimp_disabled():
        # No SiteConfiguration mailchimp keys set.
        user = _user_with_profile()
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            subscribe_user(user)
        spy.assert_not_called()

    def it_no_ops_when_already_subscribed(mailchimp_configured):
        from django.utils import timezone

        user = _user_with_profile()
        user.profile.subscribed_to_mailchimp_at = timezone.now()
        user.profile.save()
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            subscribe_user(user)
        spy.assert_not_called()

    def it_pushes_with_account_signup_tag(mailchimp_configured):
        user = _user_with_profile(
            first_attendance_status=UserProfile.FirstAttendance.FIRST_TIME,
            referral_source=UserProfile.Referral.GOOGLE,
            interest_category_slugs=["wood"],
        )
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe", return_value=True) as spy:
            subscribe_user(user)
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["email"] == "ada@example.com"
        assert "account-signup" in kwargs["tags"]
        assert "persona-first_time" in kwargs["tags"]
        assert "referral-google" in kwargs["tags"]
        assert "interest-wood" in kwargs["tags"]

    def it_stamps_subscribed_at_on_success(mailchimp_configured):
        user = _user_with_profile()
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe", return_value=True):
            subscribe_user(user)
        user.profile.refresh_from_db()
        assert user.profile.subscribed_to_mailchimp_at is not None

    def it_does_not_stamp_on_failure(mailchimp_configured):
        user = _user_with_profile()
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe", return_value=False):
            subscribe_user(user)
        user.profile.refresh_from_db()
        assert user.profile.subscribed_to_mailchimp_at is None
