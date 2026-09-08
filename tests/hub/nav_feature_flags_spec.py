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
    def it_shows_the_my_tab_link_when_enabled_and_no_billing_nav(client: Client):
        # My Tab enabled → the member My Tab link shows. Payments and Reports no longer
        # live in the sidebar in either flag state — they moved to Admin Tools cards.
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/tab/"' in body
        assert reverse("billing_admin_dashboard").encode() not in body
        assert reverse("billing_admin_reports").encode() not in body

    def it_hides_the_my_tab_link_when_disabled_and_still_no_billing_nav(client: Client):
        # The flag scopes the MEMBER My Tab surfaces; the Payments/Reports sidebar links
        # are simply gone (relocated to Admin Tools), independent of the flag.
        config = SiteConfiguration.load()
        config.my_tab_enabled = False
        config.save()
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/tab/"' not in body
        assert reverse("billing_admin_dashboard").encode() not in body
        assert reverse("billing_admin_reports").encode() not in body


def describe_hub_nav_help_and_wiki_flags():
    def it_shows_the_help_link_by_default(client: Client):
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/help/"' in body

    def it_hides_the_help_link_when_help_page_disabled(client: Client):
        config = SiteConfiguration.load()
        config.help_page_enabled = False
        config.save()
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/help/"' not in body

    def it_no_longer_puts_the_external_wiki_in_the_nav(client: Client, settings):
        """The sidebar Wiki slot belongs to the in-app wiki now (the round's locked decision).

        The external MediaWiki is demoted to a card on the wiki home for the length of the
        migration, still behind wiki_link_enabled, and retired with that same toggle.
        """
        settings.MAKERSPACE_WIKI_URL = "https://wiki.example.test"
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="https://wiki.example.test"' not in body
        assert b'href="/help/"' in body  # the Help link is unaffected

    def it_shows_the_in_app_wiki_link_once_the_wiki_is_enabled(client: Client):
        config = SiteConfiguration.load()
        config.wiki_enabled = True
        config.save()
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/wiki/" class="hub-sidebar__link' in body

    def it_hides_the_wiki_link_while_the_wiki_is_disabled(client: Client):
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/wiki/" class="hub-sidebar__link' not in body
        assert b'href="/help/"' in body  # the Help link is unaffected
