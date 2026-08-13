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

    def it_offers_a_preview_button_wired_to_the_visual_endpoint(client: Client):
        _superuser(client)
        body = client.get(f"{URL}?tab=emails").content.decode()
        # Each row's Preview button HTMX-loads the branded email into the shared modal.
        assert reverse("hub_admin_notification_visual", args=["voting.closing_soon"]) in body
        assert 'hx-target="#email-preview-body"' in body
        assert 'id="email-preview-body"' in body  # the modal is included once

    def it_is_admin_only(client: Client):
        from django.contrib.auth.models import User

        User.objects.create_user(username="plain", email="plain@x.com", password="p")
        client.login(username="plain", password="p")
        resp = client.get(f"{URL}?tab=emails")
        assert resp.status_code in (302, 403)


def describe_email_visual_preview():
    def it_renders_the_branded_email_against_sample_data(client: Client):
        _superuser(client)
        url = reverse("hub_admin_notification_visual", args=["voting.closing_soon"])
        resp = client.get(url)
        assert resp.status_code == 200
        body = resp.content.decode()
        # The framed, branded email (light shell for the voting reminders) with its subject.
        assert 'class="pl-email-preview__frame"' in body
        assert "srcdoc=" in body
        assert "favicon.png" in body  # light-shell logo, attribute-escaped inside srcdoc
        assert "Subject" in body

    def it_renders_a_framed_transactional_email(client: Client):
        _superuser(client)
        url = reverse("hub_admin_notification_visual", args=["registration_confirmed"])
        body = client.get(url).content.decode()
        assert 'class="pl-email-preview__frame"' in body
        # Since the v1.0.0 white-background rebrand, transactional emails carry the same
        # branded logo as the rest — the old cream/gold dark card is gone.
        assert "favicon.png" in body

    def it_is_admin_only(client: Client):
        from django.contrib.auth.models import User

        User.objects.create_user(username="plain2", email="plain2@x.com", password="p")
        client.login(username="plain2", password="p")
        resp = client.get(reverse("hub_admin_notification_visual", args=["voting.closing_soon"]))
        assert resp.status_code in (302, 403)
