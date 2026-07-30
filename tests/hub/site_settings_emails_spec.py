"""Site Settings → Emails tab — the read-and-route email catalogue.

The tab is a listing with deep links (no form POST), so these cover the GET render:
admin sees every email grouped by area with its edit/adjust links; a non-admin can't.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db

URL = reverse("hub_admin_site_settings")


def _superuser(client: Client):
    from django.contrib.auth.models import User

    User.objects.create_superuser(username="emailadmin", email="emailadmin@x.com", password="p")
    client.login(username="emailadmin", password="p")


def describe_emails_tab():
    def it_renders_the_tab_for_an_admin_with_the_email_catalogue(client: Client):
        _superuser(client)
        resp = client.get(f"{URL}?tab=emails")
        assert resp.status_code == 200
        body = resp.content.decode()
        # The tab button and a known email + its category are present.
        assert ">\n      Emails\n    </button>" in body or "Emails" in body
        assert "Officer heads-up" in body  # the new officer email's label
        # Deep links to the copy editor and the voting-settings adjust target.
        assert reverse("hub_admin_notification_edit", args=["voting.closing_soon", "email"]) in body
        assert reverse("hub_admin_voting_settings") in body

    def it_is_admin_only(client: Client):
        from django.contrib.auth.models import User

        User.objects.create_user(username="plain", email="plain@x.com", password="p")
        client.login(username="plain", password="p")
        resp = client.get(f"{URL}?tab=emails")
        assert resp.status_code in (302, 403)
