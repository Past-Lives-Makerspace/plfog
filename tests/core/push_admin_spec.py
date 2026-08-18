"""Admin push diagnostics: resolve a member, inventory devices, tally a test send."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from allauth.account.models import EmailAddress

from core import push_admin
from core.models import FcmDevice, PushSubscription

pytestmark = pytest.mark.django_db


def _user(username="m", email="m@example.com"):
    return User.objects.create_user(username=username, email=email)


def _device(user, token):
    return FcmDevice.objects.create(user=user, token=token, platform=FcmDevice.Platform.ANDROID)


def _sub(user, endpoint):
    return PushSubscription.objects.create(user=user, endpoint=endpoint, p256dh="k", auth="a")


def describe_resolve_user():
    def it_matches_a_linked_email_alias_first():
        user = _user(email="primary@example.com")
        EmailAddress.objects.create(user=user, email="alias@example.com", verified=True, primary=False)
        assert push_admin.resolve_user("alias@example.com") == user

    def it_matches_a_bare_user_email_when_no_alias_row_exists():
        user = _user(email="bare@example.com")
        assert push_admin.resolve_user("BARE@example.com") == user  # case-insensitive

    def it_returns_none_for_an_unknown_email():
        assert push_admin.resolve_user("nobody@example.com") is None


def describe_status_for():
    def it_lists_devices_newest_first_and_counts_web_subscriptions():
        user = _user()
        _device(user, "old")
        new = _device(user, "new")
        # updated_at is auto_now; touch `new` last so it sorts first.
        new.save()
        _sub(user, "https://push/1")
        _sub(user, "https://push/2")

        status = push_admin.status_for(user)

        assert [d.token for d in status.fcm_devices] == ["new", "old"]
        assert status.web_subscriptions == 2
        assert status.total_devices == 4
        assert status.has_any is True

    def it_reports_no_devices_for_a_member_with_none():
        status = push_admin.status_for(_user())
        assert status.has_any is False
        assert status.total_devices == 0


def describe_send_test_push():
    def it_counts_every_device_as_delivered_when_all_succeed():
        user = _user()
        _device(user, "d1")
        _sub(user, "https://push/1")
        with (
            patch("core.push_admin.send_fcm", return_value=True),
            patch("core.push_admin.send_web_push", return_value=True),
        ):
            result = push_admin.send_test_push(user, url="/x/")
        assert result.attempted == 2
        assert result.delivered == 2
        assert result.all_delivered is True

    def it_counts_only_the_successful_sends():
        user = _user()
        _device(user, "d1")
        _device(user, "d2")
        _sub(user, "https://push/1")
        # One native token delivers, the other and the browser sub fail (dead/reaped).
        with (
            patch("core.push_admin.send_fcm", side_effect=[True, False]),
            patch("core.push_admin.send_web_push", return_value=False),
        ):
            result = push_admin.send_test_push(user, url="/x/")
        assert result.attempted == 3
        assert result.delivered == 1
        assert result.all_delivered is False

    def it_reports_nothing_attempted_for_a_member_with_no_devices():
        result = push_admin.send_test_push(_user(), url="/x/")
        assert result.attempted == 0
        assert result.delivered == 0
        assert result.all_delivered is False
