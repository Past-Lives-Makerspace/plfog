"""BDD specs for Google Analytics coverage of the member hub.

The hub renders from ``templates/hub/base.html``, which was the one member-facing
layout the GA include never reached. It also navigates with ``hx-boost``, so a plain
gtag snippet reports a single page_view per session; see the include for the fix.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from core.models import SiteConfiguration


@pytest.mark.django_db
def describe_google_analytics_on_the_hub():
    def _sign_in(client: Client) -> None:
        User.objects.create_user(username="hubmember", password="pass")
        client.login(username="hubmember", password="pass")

    def it_omits_the_tag_when_no_measurement_id_is_set(client: Client):
        _sign_in(client)

        response = client.get("/feedback/")

        assert response.status_code == 200
        assert b"googletagmanager.com" not in response.content

    def it_injects_the_tag_when_configured(client: Client):
        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-HUB12345"
        config.save()
        _sign_in(client)

        response = client.get("/feedback/")

        assert response.status_code == 200
        assert b"googletagmanager.com" in response.content
        assert b"G-HUB12345" in response.content

    def it_sends_a_page_view_on_boosted_navigation(client: Client):
        config = SiteConfiguration.load()
        config.google_analytics_measurement_id = "G-HUB12345"
        config.save()
        _sign_in(client)

        response = client.get("/feedback/")
        body = response.content.decode()

        # Without these, hx-boost navigation is invisible to GA.
        assert "htmx:afterSettle" in body
        assert "htmx:historyRestore" in body
        assert "'page_view'" in body
