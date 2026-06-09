"""Browser push sending with dead-subscription cleanup."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from pywebpush import WebPushException

from core import push
from core.models import PushSubscription

pytestmark = pytest.mark.django_db


def _sub(user):
    return PushSubscription.objects.create(
        user=user,
        endpoint="https://push.example/abc",
        p256dh="key",
        auth="auth",
    )


def describe_send_web_push():
    def it_calls_pywebpush_with_payload():
        user = User.objects.create_user(username="u", email="u@example.com")
        sub = _sub(user)
        with patch("core.push.webpush") as mock_wp:
            push.send_web_push(sub, title="Hi", body="There", url="/x/")
        mock_wp.assert_called_once()

    def it_deletes_subscription_on_410_gone():
        user = User.objects.create_user(username="u2", email="u2@example.com")
        sub = _sub(user)

        class _Resp:
            status_code = 410

        with patch("core.push.webpush", side_effect=WebPushException("gone", response=_Resp())):
            push.send_web_push(sub, title="Hi", body="There", url="/x/")
        assert not PushSubscription.objects.filter(pk=sub.pk).exists()

    def it_swallows_other_errors_without_deleting():
        user = User.objects.create_user(username="u3", email="u3@example.com")
        sub = _sub(user)

        class _Resp:
            status_code = 500

        with patch("core.push.webpush", side_effect=WebPushException("boom", response=_Resp())):
            push.send_web_push(sub, title="Hi", body="There", url="/x/")
        assert PushSubscription.objects.filter(pk=sub.pk).exists()
