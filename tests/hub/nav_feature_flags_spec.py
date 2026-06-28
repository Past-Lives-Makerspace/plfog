"""Render specs for the Site Settings → Features nav gating in the hub sidebar."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def _login_admin(client: Client) -> User:
    user = User.objects.create_superuser(username="navadmin", email="navadmin@x.com", password="p")
    client.login(username="navadmin", password="p")
    return user


def describe_hub_nav_feature_flags():
    def it_shows_tab_and_payments_nav_when_enabled(client: Client):
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/tab/"' in body
        assert reverse("billing_admin_dashboard").encode() in body
        assert reverse("billing_admin_reports").encode() in body

    def it_hides_tab_and_payments_nav_when_disabled(client: Client):
        config = SiteConfiguration.load()
        config.tab_payments_enabled = False
        config.save()
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/tab/"' not in body
        assert reverse("billing_admin_dashboard").encode() not in body
        assert reverse("billing_admin_reports").encode() not in body
