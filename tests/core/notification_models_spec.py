"""BDD-style tests for Notification + NotificationPreference (per-channel shape)."""

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError

from core.models import Notification, NotificationPreference

pytestmark = pytest.mark.django_db


def describe_Notification():
    def it_starts_unread():
        user = User.objects.create_user(username="u", email="u@example.com")
        n = Notification.objects.create(
            user=user,
            trigger="class_published",
            title="New class",
            body="A class went live",
            url="/x/",
        )
        assert n.read_at is None
        assert n.is_unread is True

    def it_marks_read():
        user = User.objects.create_user(username="u2", email="u2@example.com")
        n = Notification.objects.create(user=user, trigger="class_published", title="t", body="b")
        n.mark_read()
        n.refresh_from_db()
        assert n.read_at is not None
        assert n.is_unread is False

    def it_str_includes_title_and_email():
        user = User.objects.create_user(username="u_str", email="strtest@example.com")
        n = Notification.objects.create(user=user, trigger="class_published", title="New class", body="b")
        assert str(n) == "New class → strtest@example.com"


def describe_NotificationPreference():
    def it_is_unique_per_user_event_and_channel():
        user = User.objects.create_user(username="u3", email="u3@example.com")
        NotificationPreference.objects.create(user=user, event_key="class_published", channel="email", enabled=True)
        with pytest.raises(IntegrityError):
            NotificationPreference.objects.create(user=user, event_key="class_published", channel="email")

    def it_allows_distinct_channels_for_the_same_event():
        user = User.objects.create_user(username="u3b", email="u3b@example.com")
        NotificationPreference.objects.create(user=user, event_key="class_published", channel="email", enabled=True)
        # A different channel for the same (user, event) is a distinct row.
        push = NotificationPreference.objects.create(
            user=user, event_key="class_published", channel="push", enabled=False
        )
        assert push.pk is not None

    def it_str_includes_email_event_channel_and_state():
        user = User.objects.create_user(username="u_pref", email="pref@example.com")
        pref = NotificationPreference.objects.create(
            user=user, event_key="lease_expiring", channel="email", enabled=False
        )
        assert str(pref) == "pref@example.com:lease_expiring/email=off"
