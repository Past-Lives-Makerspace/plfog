"""Hub push-test tool: inspect a member's push devices and fire a test push (admin-only)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core.models import FcmDevice

User = get_user_model()

pytestmark = pytest.mark.django_db

_URL = "/announcements/push-test/"


@pytest.fixture()
def admin_client():
    user = User.objects.create_superuser(username="pt-admin", password="pw", email="pt-admin@example.com")
    client = Client()
    client.force_login(user)
    return client


def _member_client(username: str = "pt-mem") -> Client:
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw")
    client = Client()
    client.force_login(user)
    return client


def describe_hub_push_test_view():
    def it_redirects_anonymous_to_login(client):
        resp = client.get(_URL)
        assert resp.status_code == 302

    def it_forbids_a_non_admin_member():
        resp = _member_client().get(_URL)
        assert resp.status_code == 403

    def it_renders_the_form_for_an_admin(admin_client):
        resp = admin_client.get(_URL)
        assert resp.status_code == 200
        assert "form" in resp.context

    def it_lists_a_members_devices_on_lookup(admin_client):
        user = User.objects.create_user(username="dev", email="dev@example.com")
        FcmDevice.objects.create(user=user, token="t1", platform=FcmDevice.Platform.ANDROID)
        resp = admin_client.post(_URL, data={"email": "dev@example.com", "lookup": "1"})
        assert resp.status_code == 200
        assert resp.context["status"].total_devices == 1
        assert resp.context["result"] is None

    def it_sends_a_test_and_reports_the_delivered_tally(admin_client):
        user = User.objects.create_user(username="snd", email="snd@example.com")
        FcmDevice.objects.create(user=user, token="t1", platform=FcmDevice.Platform.ANDROID)
        with patch("core.push_admin.send_fcm", return_value=True):
            resp = admin_client.post(_URL, data={"email": "snd@example.com", "send": "1"})
        assert resp.status_code == 200
        assert resp.context["result"].delivered == 1
        assert resp.context["result"].attempted == 1

    def it_reports_zero_attempted_when_the_member_has_no_devices(admin_client):
        User.objects.create_user(username="empty", email="empty@example.com")
        resp = admin_client.post(_URL, data={"email": "empty@example.com", "send": "1"})
        assert resp.status_code == 200
        assert resp.context["result"].attempted == 0

    def it_re_renders_with_an_error_for_an_unknown_email(admin_client):
        resp = admin_client.post(_URL, data={"email": "ghost@example.com", "lookup": "1"})
        assert resp.status_code == 200
        assert resp.context["status"] is None
        assert resp.context["form"].errors
