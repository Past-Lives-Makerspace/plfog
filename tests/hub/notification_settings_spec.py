"""Notifications settings tab saves NotificationPreference rows."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import NotificationPreference

pytestmark = pytest.mark.django_db


def describe_notifications_tab():
    def it_saves_push_and_email_toggles(client):
        User.objects.create_user(username="m", email="m@example.com", password="pw12345!")
        client.login(username="m", password="pw12345!")
        client.post(
            reverse("hub_user_settings"),
            {
                "form_id": "notifications",
                "pref__class_published__push": "on",
                "pref__tab_charged__email": "on",
            },
        )
        user = User.objects.get(username="m")
        assert (
            NotificationPreference.objects.get(user=user, event_key="class_published", channel="push").enabled is True
        )
        assert NotificationPreference.objects.get(user=user, event_key="tab_charged", channel="email").enabled is True

    def it_clears_unchecked_toggles(client):
        user = User.objects.create_user(username="m2", email="m2@example.com", password="pw12345!")
        NotificationPreference.objects.create(user=user, event_key="class_published", channel="push", enabled=True)
        client.login(username="m2", password="pw12345!")
        client.post(reverse("hub_user_settings"), {"form_id": "notifications"})  # nothing checked
        assert (
            NotificationPreference.objects.get(user=user, event_key="class_published", channel="push").enabled is False
        )
