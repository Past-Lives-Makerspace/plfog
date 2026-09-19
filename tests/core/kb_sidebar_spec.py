"""The Knowledge Base sidebar entry.

Rendered through a real client, as both an admin and a plain member, because base.html writes the
sidebar **twice** and the two blocks serve different viewers: an entry added to one of them is
invisible to half the roster while looking correct in a diff and on whichever page its author
happened to load. Scraping the template for a substring counts include sites; it cannot tell you
that a member sees one.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory, UserFactory

pytestmark = pytest.mark.django_db

KB_URL = "https://kb.example.test"


def _login_admin(client: Client):
    from django.contrib.auth.models import User

    user = User.objects.create_superuser(username="kbadmin", email="kbadmin@x.com", password="p")
    client.login(username="kbadmin", password="p")
    return user


def _login_member(client: Client, username: str = "kbmember"):
    MembershipPlanFactory()
    user = UserFactory(username=username)
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    client.force_login(user)
    return user


# The sidebar entry, identified by the attribute only it carries. Asserting on the words
# "Knowledge Base" matches the changelog modal too — this PR's own fragment says them.
ENTRY = 'data-help-key="nav.knowledge-base"'


def _sidebar(client: Client) -> str:
    SiteConfiguration.load()
    return client.get(reverse("hub_home"), follow=True).content.decode()


def describe_the_sidebar_entry():
    def it_is_shown_to_an_admin(client: Client, settings):
        settings.KNOWLEDGE_BASE_URL = KB_URL
        _login_admin(client)
        html = _sidebar(client)
        assert ENTRY in html
        assert KB_URL in html

    def it_is_shown_to_a_plain_member(client: Client, settings):
        """The half of the roster a one-block entry would have missed."""
        settings.KNOWLEDGE_BASE_URL = KB_URL
        _login_member(client)
        html = _sidebar(client)
        assert ENTRY in html
        assert KB_URL in html


def describe_when_no_url_is_configured():
    def it_renders_no_entry_for_a_member(client: Client, settings):
        """The bug this guards: the feature switch reads ON for an unseeded key, and the shared
        partial renders `<a href="{{ url }}">` without testing `url` — so a blank setting used to
        give every member a Knowledge Base link that reloaded the page they were on."""
        settings.KNOWLEDGE_BASE_URL = ""
        _login_member(client)
        assert ENTRY not in _sidebar(client)

    def it_renders_no_entry_for_an_admin(client: Client, settings):
        settings.KNOWLEDGE_BASE_URL = ""
        _login_admin(client)
        assert ENTRY not in _sidebar(client)

    def it_never_emits_an_empty_href(client: Client, settings):
        settings.KNOWLEDGE_BASE_URL = ""
        _login_member(client)
        assert 'href=""' not in _sidebar(client)


def describe_the_feature_switch():
    def it_is_registered_so_the_sidebar_can_resolve_it():
        from core.features import FEATURES_BY_KEY

        assert "knowledge_base" in FEATURES_BY_KEY

    def it_hides_the_entry_when_turned_off(client: Client, settings):
        from tests.features import hide

        settings.KNOWLEDGE_BASE_URL = KB_URL
        hide("knowledge_base")
        _login_member(client)
        assert ENTRY not in _sidebar(client)

    def it_is_not_the_access_gate():
        """Turning the switch off hides the way in; it does not close the KB, which is a separate
        application with its own tiers. The off_description has to say so, because an admin
        reading that card is deciding whether they have just revoked someone's access."""
        from core.features import FEATURES_BY_KEY

        assert "own access tiers" in FEATURES_BY_KEY["knowledge_base"].off_description


def describe_the_help_key():
    def it_is_registered_so_the_bubble_can_resolve_it():
        """`help_registry.entry()` raises KeyError on an unknown key, and the repo-wide help-key
        lint cannot see one that arrives as an include parameter."""
        from core.help_registry import HELP_KEYS

        assert "nav.knowledge-base" in HELP_KEYS


def describe_the_shipped_default():
    """Every spec above sets ``KNOWLEDGE_BASE_URL`` before asserting on it, which is why a blank
    default shipped and the entry was invisible in production while the suite stayed green. These
    two assert the value the app carries when nobody configures anything."""

    def it_is_a_real_address():
        """A host, not just a scheme: ``startswith("https://")`` alone passes for ``"https://"``,
        which would render an entry pointing at nothing."""
        from urllib.parse import urlparse

        from plfog.settings import DEFAULT_KNOWLEDGE_BASE_URL

        parsed = urlparse(DEFAULT_KNOWLEDGE_BASE_URL)
        assert parsed.scheme == "https"
        assert parsed.netloc

    def it_renders_the_entry_with_nothing_configured(client: Client):
        """No ``settings.KNOWLEDGE_BASE_URL =`` line on purpose: this is the deployed behaviour,
        and it fails if the default goes back to blank."""
        _login_member(client)
        assert ENTRY in _sidebar(client)
