"""Bell feed endpoints."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Notification

pytestmark = pytest.mark.django_db


def _login(client, n):
    user = User.objects.create_user(username=f"u{n}", email=f"u{n}@example.com", password="pw12345!")
    client.login(username=f"u{n}", password="pw12345!")
    return user


def describe_notification_feed():
    def it_lists_recent_notifications(client):
        user = _login(client, 1)
        Notification.objects.create(user=user, trigger="x", title="Hello", body="b")
        resp = client.get(reverse("notification_feed"))
        assert resp.status_code == 200
        assert b"Hello" in resp.content

    def it_only_shows_my_notifications(client):
        user = _login(client, 2)
        other = User.objects.create_user(username="other", email="o@example.com")
        Notification.objects.create(user=other, trigger="x", title="Secret", body="b")
        resp = client.get(reverse("notification_feed"))
        assert b"Secret" not in resp.content


def describe_unread_count():
    def it_returns_the_count(client):
        user = _login(client, 3)
        Notification.objects.create(user=user, trigger="x", title="t", body="b")
        resp = client.get(reverse("notification_unread_count"))
        assert b"1" in resp.content


def describe_mark_read():
    def it_marks_one_read_and_redirects_to_url(client):
        user = _login(client, 4)
        n = Notification.objects.create(user=user, trigger="x", title="t", body="b", url="/tab/")
        resp = client.post(reverse("notification_read", args=[n.pk]))
        n.refresh_from_db()
        assert n.read_at is not None
        assert resp.status_code == 302
        assert resp.url == "/tab/"

    def it_marks_all_read(client):
        user = _login(client, 5)
        Notification.objects.create(user=user, trigger="x", title="t", body="b")
        Notification.objects.create(user=user, trigger="y", title="t2", body="b")
        client.post(reverse("notification_read_all"))
        assert Notification.objects.filter(user=user, read_at__isnull=True).count() == 0
