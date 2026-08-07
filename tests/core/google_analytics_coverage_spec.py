"""BDD specs pinning Google Analytics coverage across every FOG surface.

Two invariants, both learned the hard way:

1. **Every surface is measured.** FOG is tracked end to end, the Django admin included.
   There is no path exclusion in the context processor.
2. **Exactly once per page.** ``classes/base_public.html`` and ``guilds/base_public.html``
   extend ``hub/base.html``. Once the shared partial moved into that parent's ``<head>``,
   their own copies became duplicates and double-counted every pageview, on load and again
   on each hx-boost navigation. The count assertions below are what catch a regression.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration

MEASUREMENT_ID = "G-COVERAGE1"


@pytest.fixture
def configured_ga(db) -> None:
    config = SiteConfiguration.load()
    config.google_analytics_measurement_id = MEASUREMENT_ID
    config.save()


@pytest.mark.django_db
def describe_google_analytics_coverage():
    def _staff(client: Client) -> None:
        user = User.objects.create_superuser(username="ga-cov", email="ga-cov@example.com", password="pass")
        client.force_login(user)

    def _member(client: Client) -> None:
        user = User.objects.create_user(username="ga-cov-member", password="pass")
        client.force_login(user)

    def it_tags_the_public_home_exactly_once(client: Client, configured_ga: None):
        body = client.get("/").content.decode()

        assert body.count("gtag('config'") == 1

    def it_tags_the_public_class_list_exactly_once(client: Client, configured_ga: None):
        response = client.get("/classes/")

        assert response.status_code == 200
        body = response.content.decode()
        # Extends hub/base.html; a second include here double-counted every pageview.
        assert body.count("googletagmanager.com/gtag/js") == 1
        assert body.count("gtag('config'") == 1

    def it_tags_the_member_hub_exactly_once(client: Client, configured_ga: None):
        _member(client)

        response = client.get("/feedback/")

        assert response.status_code == 200
        body = response.content.decode()
        assert body.count("gtag('config'") == 1

    def it_tags_the_django_admin_exactly_once(client: Client, configured_ga: None):
        _staff(client)

        response = client.get("/admin/")

        assert response.status_code == 200
        body = response.content.decode()
        assert body.count("gtag('config'") == 1
        assert MEASUREMENT_ID in body

    def it_tags_allauth_pages_we_do_not_override(client: Client, configured_ga: None):
        # Password reset renders through allauth's own layout, which we fork at
        # templates/allauth/layouts/base.html purely to add the include. If an allauth
        # upgrade drops that fork, this is what notices.
        response = client.get(reverse("account_reset_password"))

        assert response.status_code == 200
        body = response.content.decode()
        assert body.count("gtag('config'") == 1
        assert MEASUREMENT_ID in body

    def it_sends_page_view_from_exactly_one_place(client: Client, configured_ga: None):
        response = client.get("/classes/")

        body = response.content.decode()
        # Counted on the GA sender itself, not on "htmx:afterSettle" — hub/base.html has its
        # own unrelated afterSettle listener for Alpine. Two senders here would fire page_view
        # twice on every hx-boost navigation.
        assert body.count("gtag('event', 'page_view'") == 1
        assert body.count("function trackPageView()") == 1
