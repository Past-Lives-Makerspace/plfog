"""Access + rendering for the /manage/activity/ staff page."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import SiteActivity, TransactionalEmailLog

pytestmark = pytest.mark.django_db


def describe_manage_activity():
    def it_redirects_anonymous_users(client):
        resp = client.get(reverse("manage_activity"))
        assert resp.status_code in (302, 301)

    def it_forbids_non_staff(client):
        User.objects.create_user(username="m", email="m@example.com", password="pw12345!")
        client.login(username="m", password="pw12345!")
        resp = client.get(reverse("manage_activity"))
        assert resp.status_code in (302, 403)

    def it_renders_for_staff(client):
        staff = User.objects.create_user(
            username="a",
            email="a@example.com",
            password="pw12345!",
            is_staff=True,
        )
        client.login(username="a", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        resp = client.get(reverse("manage_activity"))
        assert resp.status_code == 200
        assert b"Site Activity" in resp.content

    def it_filters_the_feed_by_kind(client):
        staff = User.objects.create_user(
            username="a2",
            email="a2@example.com",
            password="pw12345!",
            is_staff=True,
        )
        client.login(username="a2", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        SiteActivity.log(SiteActivity.Kind.LOGOUT, actor=staff)
        resp = client.get(reverse("manage_activity"), {"kind": "login"})
        assert resp.status_code == 200

    def it_filters_the_feed_by_actor(client):
        staff = User.objects.create_user(
            username="a3",
            email="a3@example.com",
            password="pw12345!",
            is_staff=True,
        )
        client.login(username="a3", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        resp = client.get(reverse("manage_activity"), {"actor": "a3@example.com"})
        assert resp.status_code == 200

    def it_filters_the_emails_by_status(client):
        User.objects.create_user(
            username="a4",
            email="a4@example.com",
            password="pw12345!",
            is_staff=True,
        )
        client.login(username="a4", password="pw12345!")
        TransactionalEmailLog.objects.create(
            to_email="x@example.com",
            subject="s",
            trigger_kind="billing.receipt",
            status=TransactionalEmailLog.Status.SENT,
        )
        resp = client.get(reverse("manage_activity"), {"tab": "emails", "status": "sent"})
        assert resp.status_code == 200
