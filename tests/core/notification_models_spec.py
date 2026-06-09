"""BDD-style tests for Notification + NotificationPreference."""

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


def describe_NotificationPreference():
    def it_is_unique_per_user_and_trigger():
        user = User.objects.create_user(username="u3", email="u3@example.com")
        NotificationPreference.objects.create(user=user, trigger="class_published", push_enabled=True)
        with pytest.raises(IntegrityError):
            NotificationPreference.objects.create(user=user, trigger="class_published")
