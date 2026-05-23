"""BDD specs for core.services.tour_status."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import UserProfile
from core.services.tour_status import force_refresh, refresh_if_stale

pytestmark = pytest.mark.django_db


def _user_with_profile(**profile_kwargs):
    User = get_user_model()
    user = User.objects.create_user(
        username=profile_kwargs.pop("username", "tour@example.com"),
        email=profile_kwargs.pop("email", "tour@example.com"),
        password="x",
    )
    UserProfile.objects.create(user=user, **profile_kwargs)
    return user


@pytest.fixture
def simplybook_enabled(settings):
    settings.SIMPLYBOOK_API_KEY = "key"
    settings.SIMPLYBOOK_COMPANY_LOGIN = "co"


def describe_refresh_if_stale():
    def it_no_ops_when_user_has_no_profile(simplybook_enabled):
        User = get_user_model()
        user = User.objects.create_user(username="x@y.com", email="x@y.com", password="x")
        with patch("core.integrations.simplybook.SimplybookClient.has_completed_tour") as spy:
            refresh_if_stale(user)
        spy.assert_not_called()

    def it_no_ops_when_simplybook_disabled():
        user = _user_with_profile()
        with patch("core.integrations.simplybook.SimplybookClient.has_completed_tour") as spy:
            refresh_if_stale(user)
        spy.assert_not_called()

    def it_refreshes_when_never_checked(simplybook_enabled):
        user = _user_with_profile()
        with patch(
            "core.integrations.simplybook.SimplybookClient.has_completed_tour",
            return_value=False,
        ) as spy:
            refresh_if_stale(user)
        spy.assert_called_once_with("tour@example.com")
        user.profile.refresh_from_db()
        assert user.profile.tour_status_checked_at is not None
        assert user.profile.completed_tour_at is None

    def it_skips_when_recently_checked(simplybook_enabled):
        user = _user_with_profile(tour_status_checked_at=timezone.now() - timedelta(hours=1))
        with patch("core.integrations.simplybook.SimplybookClient.has_completed_tour") as spy:
            refresh_if_stale(user, max_age_hours=24)
        spy.assert_not_called()

    def it_writes_completed_tour_at_when_found(simplybook_enabled):
        user = _user_with_profile()
        with patch(
            "core.integrations.simplybook.SimplybookClient.has_completed_tour",
            return_value=True,
        ):
            refresh_if_stale(user)
        user.profile.refresh_from_db()
        assert user.profile.completed_tour_at is not None


def describe_force_refresh():
    def it_polls_even_when_recently_checked(simplybook_enabled):
        user = _user_with_profile(tour_status_checked_at=timezone.now())
        with patch(
            "core.integrations.simplybook.SimplybookClient.has_completed_tour",
            return_value=False,
        ) as spy:
            force_refresh(user)
        spy.assert_called_once()

    def it_no_ops_when_user_has_no_profile(simplybook_enabled):
        # Line 41: force_refresh returns early when profile is None.
        User = get_user_model()
        user = User.objects.create_user(username="noprofile@example.com", email="noprofile@example.com", password="x")
        with patch("core.integrations.simplybook.SimplybookClient.has_completed_tour") as spy:
            force_refresh(user)
        spy.assert_not_called()


def describe__refresh():
    def it_no_ops_when_user_email_is_blank(simplybook_enabled):
        # Line 53: _refresh returns early when email strips to empty.
        user = _user_with_profile(username="blank@example.com", email="")
        # Give the profile a blank email by blanking the user's email directly.
        user.email = ""
        user.save()
        with patch("core.integrations.simplybook.SimplybookClient.has_completed_tour") as spy:
            # force_refresh calls _refresh unconditionally.
            force_refresh(user)
        spy.assert_not_called()
