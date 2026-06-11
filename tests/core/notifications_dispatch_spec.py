"""dispatch() — in-app always; push/email per preference; force_email override."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from core import notifications
from core.models import Notification, NotificationPreference, PushSubscription, TransactionalEmailLog

pytestmark = pytest.mark.django_db


def _user(n):
    return User.objects.create_user(username=f"u{n}", email=f"u{n}@example.com")


def describe_dispatch():
    def it_always_creates_in_app_rows():
        users = [_user(1), _user(2)]
        notifications.dispatch("class_published", users, title="New class", body="b", url="/x/")
        assert Notification.objects.count() == 2

    def it_sends_push_only_to_opted_in_users():
        u = _user(3)
        NotificationPreference.objects.create(user=u, trigger="class_published", push_enabled=True)
        PushSubscription.objects.create(user=u, endpoint="https://p/x", p256dh="k", auth="a")
        with patch("core.notifications.send_web_push") as mock_push:
            notifications.dispatch("class_published", [u], title="t", body="b")
        mock_push.assert_called_once()

    def it_does_not_push_without_a_preference():
        u = _user(4)
        PushSubscription.objects.create(user=u, endpoint="https://p/y", p256dh="k", auth="a")
        with patch("core.notifications.send_web_push") as mock_push:
            notifications.dispatch("class_published", [u], title="t", body="b")
        mock_push.assert_not_called()

    def it_emails_opted_in_users_through_core_email():
        u = _user(5)
        NotificationPreference.objects.create(user=u, trigger="class_published", email_enabled=True)
        notifications.dispatch("class_published", [u], title="t", body="b")
        assert TransactionalEmailLog.objects.filter(trigger_kind="notification.class_published").exists()

    def it_force_emails_regardless_of_preference():
        u = _user(6)
        notifications.dispatch("new_login", [u], title="New login", body="b")
        assert TransactionalEmailLog.objects.filter(trigger_kind="notification.new_login").exists()

    def it_skips_users_without_email_when_emailing():
        u = User.objects.create_user(username="noemail", email="")
        NotificationPreference.objects.create(user=u, trigger="class_published", email_enabled=True)
        notifications.dispatch("class_published", [u], title="t", body="b")
        assert not TransactionalEmailLog.objects.exists()
