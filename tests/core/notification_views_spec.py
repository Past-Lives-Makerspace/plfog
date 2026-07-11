"""Notifications page + bell-link + mark-read endpoints."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Notification

pytestmark = pytest.mark.django_db


def _login(client, n):
    user = User.objects.create_user(username=f"u{n}", email=f"u{n}@example.com", password="pw12345!")
    client.login(username=f"u{n}", password="pw12345!")
    return user


def describe_notification_list():
    def it_renders_the_members_own_notifications(client):
        user = _login(client, 1)
        Notification.objects.create(user=user, trigger="x", title="Hello", body="b")
        resp = client.get(reverse("notification_list"))
        assert resp.status_code == 200
        assert b"Hello" in resp.content

    def it_does_not_show_other_users_notifications(client):
        _login(client, 2)
        other = User.objects.create_user(username="other", email="o@example.com")
        Notification.objects.create(user=other, trigger="x", title="Secret", body="b")
        resp = client.get(reverse("notification_list"))
        assert b"Secret" not in resp.content

    def it_emphasizes_unread_rows(client):
        user = _login(client, 3)
        Notification.objects.create(user=user, trigger="x", title="Unread one", body="b")
        read = Notification.objects.create(user=user, trigger="y", title="Read one", body="b")
        read.mark_read()
        content = client.get(reverse("notification_list")).content.decode()
        # Both rows render, but only the unread one carries the emphasis class.
        assert "Unread one" in content
        assert "Read one" in content
        assert content.count("pl-note--unread") == 1

    def it_paginates_at_20(client):
        user = _login(client, 4)
        for i in range(25):
            Notification.objects.create(user=user, trigger="x", title=f"N{i}", body="b")
        first = client.get(reverse("notification_list")).content.decode()
        assert first.count("pl-note__btn") == 20
        assert "Page 1 of 2" in first
        second = client.get(reverse("notification_list"), {"page": 2}).content.decode()
        assert second.count("pl-note__btn") == 5

    def it_shows_the_empty_state_when_none(client):
        _login(client, 5)
        content = client.get(reverse("notification_list")).content.decode()
        assert "You're all caught up." in content
        assert "pl-note__btn" not in content

    def it_shows_mark_all_only_when_unread_exists(client):
        # Assert on the mark-all form's action URL, not the visible button text — the hub
        # "what's new" widget echoes the CHANGELOG (which mentions "Mark all as read") on
        # every page, so the label alone would false-positive.
        user = _login(client, 6)
        mark_all_action = f'action="{reverse("notification_read_all")}"'
        note = Notification.objects.create(user=user, trigger="x", title="t", body="b")
        with_unread = client.get(reverse("notification_list")).content.decode()
        assert mark_all_action in with_unread
        note.mark_read()
        all_read = client.get(reverse("notification_list")).content.decode()
        assert mark_all_action not in all_read

    def it_requires_login(client):
        resp = client.get(reverse("notification_list"))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url


def describe_notification_read():
    def it_marks_one_read_and_redirects_to_its_url(client):
        user = _login(client, 7)
        n = Notification.objects.create(user=user, trigger="x", title="t", body="b", url="/tab/")
        resp = client.post(reverse("notification_read", args=[n.pk]))
        n.refresh_from_db()
        assert n.read_at is not None
        assert resp.status_code == 302
        assert resp.url == "/tab/"


def describe_notification_read_all():
    def it_marks_all_read_and_redirects_to_the_page(client):
        user = _login(client, 8)
        Notification.objects.create(user=user, trigger="x", title="t", body="b")
        Notification.objects.create(user=user, trigger="y", title="t2", body="b")
        resp = client.post(reverse("notification_read_all"))
        assert Notification.objects.filter(user=user, read_at__isnull=True).count() == 0
        assert resp.status_code == 302
        assert resp.url == reverse("notification_list")


def describe_unread_count():
    def it_returns_the_count(client):
        user = _login(client, 9)
        Notification.objects.create(user=user, trigger="x", title="t", body="b")
        resp = client.get(reverse("notification_unread_count"))
        assert b"1" in resp.content


def describe_notification_bell():
    def it_renders_as_a_link_with_the_badge(client):
        user = _login(client, 10)
        Notification.objects.create(user=user, trigger="x", title="t", body="b")
        content = client.get(reverse("notification_list")).content.decode()
        # The bell is a plain link to the page, carrying its unread badge...
        assert 'href="/notifications/"' in content
        assert "pl-bell__btn" in content
        assert "pl-bell__badge" in content
        # ...and none of the removed dropdown markup remains.
        assert "pl-bell__panel" not in content
