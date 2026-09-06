"""BDD specs for the Admin Tools hub — the relocated Payments and Reports cards.

Payments and Reports moved off the sidebar into Admin Tools as two cards gated by
``has_billing_admin_access`` (fog admin OR ``BILLING_APPROVER``). A capability-only
member now also gets into Admin Tools at all (the new ``_can_use_admin_tools`` arm),
closing the "could open the pages by URL but had no navigation" gap.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import AdminCapability
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_PAYMENTS_HREF = reverse("billing_admin_dashboard")
_REPORTS_HREF = reverse("billing_admin_reports")
_MEMBERS_HREF = reverse("hub_admin_members")
_SETTINGS_HREF = reverse("hub_admin_site_settings")


def _login_user(client: Client, username: str) -> User:
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _login_billing_approver(client: Client, username: str = "biller") -> User:
    user = _login_user(client, username)
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)  # type: ignore[attr-defined]
    return user


def _login_guild_lead(client: Client, username: str = "lead") -> User:
    user = _login_user(client, username)
    GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
    return user


def _login_superuser(client: Client, username: str = "fogadmin") -> User:
    User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return User.objects.get(username=username)


def describe_admin_tools_billing_cards():
    def describe_a_billing_approver_only_member():
        def it_sees_the_admin_tools_sidebar_entry(client: Client):
            _login_billing_approver(client, "appr_sidebar")
            body = client.get(reverse("hub_member_directory")).content.decode()
            assert reverse("hub_admin_tools") in body

        def it_reaches_the_admin_tools_page(client: Client):
            _login_billing_approver(client, "appr_page")
            assert client.get(reverse("hub_admin_tools")).status_code == 200

        def it_sees_the_payments_and_reports_cards(client: Client):
            _login_billing_approver(client, "appr_cards")
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert _PAYMENTS_HREF in content
            assert _REPORTS_HREF in content

        def it_sees_none_of_the_admin_only_cards(client: Client):
            _login_billing_approver(client, "appr_noadmin")
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert _MEMBERS_HREF not in content
            assert _SETTINGS_HREF not in content

    def describe_a_guild_lead_without_the_capability():
        def it_reaches_the_page_but_sees_no_billing_cards(client: Client):
            _login_guild_lead(client, "lead_nocards")
            response = client.get(reverse("hub_admin_tools"))
            assert response.status_code == 200
            content = response.content.decode()
            assert _PAYMENTS_HREF not in content
            assert _REPORTS_HREF not in content

    def describe_a_fog_admin():
        def it_sees_both_billing_cards(client: Client):
            _login_superuser(client, "admin_cards")
            content = client.get(reverse("hub_admin_tools")).content.decode()
            assert _PAYMENTS_HREF in content
            assert _REPORTS_HREF in content

        def it_is_bounced_home_when_previewing_as_a_plain_member(client: Client):
            _login_superuser(client, "admin_preview")
            session = client.session
            session["view_as_role"] = "member"
            session.save()
            response = client.get(reverse("hub_admin_tools"))
            assert response.status_code == 302
            assert response.url == reverse("hub_home")

    def describe_card_access_parity():
        """A billing card shows iff its target view admits the viewer (billing_admin_access_required)."""

        def it_holds_for_an_approver(client: Client):
            _login_billing_approver(client, "parity_appr")
            has_card = _PAYMENTS_HREF in client.get(reverse("hub_admin_tools")).content.decode()
            admitted = client.get(_PAYMENTS_HREF).status_code == 200
            assert has_card is True
            assert has_card == admitted

        def it_holds_for_a_guild_lead(client: Client):
            _login_guild_lead(client, "parity_lead")
            has_card = _PAYMENTS_HREF in client.get(reverse("hub_admin_tools")).content.decode()
            admitted = client.get(_PAYMENTS_HREF).status_code == 200
            assert has_card is False
            assert has_card == admitted

        def it_holds_for_a_fog_admin(client: Client):
            _login_superuser(client, "parity_admin")
            has_card = _PAYMENTS_HREF in client.get(reverse("hub_admin_tools")).content.decode()
            admitted = client.get(_PAYMENTS_HREF).status_code == 200
            assert has_card is True
            assert has_card == admitted


def _tools_grid(client: Client) -> str:
    """Just the tool card grid from /manage/tools/, not the whole page.

    Scoped on purpose. The rendered changelog rides in every hub page's context, and a
    v1.3.0 entry still says Admin Tools links each role's Quickstart guide — true when it
    shipped, and not something to rewrite. A negative assertion against the whole body
    would be reading release history instead of the grid.
    """
    body = client.get(reverse("hub_admin_tools")).content.decode()
    grid = re.search(r'<div class="pl-tools-grid".*?\n</div>', body, re.S)
    assert grid is not None, "the tools grid did not render"
    return grid.group(0)


def _card_titles(client: Client) -> list[str]:
    """The tool card titles on /manage/tools/, in the order the page renders them."""
    return re.findall(r'pl-tool-card__title">([^<]+)<', _tools_grid(client))


def describe_admin_tools_card_order():
    """The grid is alphabetical, and the two Quickstart guide cards are gone.

    Order is asserted against ``sorted()`` rather than a hardcoded list on purpose: a
    card added in the wrong place fails here without anyone remembering to edit a
    fixture, which is the whole point of the change.
    """

    def it_lists_a_fog_admins_cards_alphabetically(client: Client):
        _login_superuser(client, "order_admin")
        titles = _card_titles(client)
        assert titles == sorted(titles)

    def it_shows_a_fog_admin_every_tool_card(client: Client):
        _login_superuser(client, "order_admin_all")
        assert _card_titles(client) == [
            "Activity",
            "Announcements",
            "Manage Classes",
            "Manage Members",
            "Notification Settings",
            "Orientations",
            "Payments",
            "Push Notification Test",
            "Reports",
            "Site Settings",
        ]

    def it_stays_alphabetical_for_a_partial_role(client: Client):
        # A guild lead sees a subset, so this catches an ordering that only holds
        # when every card happens to render.
        _login_guild_lead(client, "order_lead")
        titles = _card_titles(client)
        assert titles == sorted(titles)
        assert titles, "a guild lead should still see at least one tool card"

    def describe_the_quickstart_guide_cards():
        def it_does_not_show_them_to_a_fog_admin(client: Client):
            _login_superuser(client, "qs_admin")
            grid = _tools_grid(client)
            assert "Quickstart" not in grid
            assert "/help/teaching/instructor-quickstart/" not in grid
            assert "/help/running-a-guild/guild-lead-quickstart/" not in grid

        def it_does_not_show_them_to_a_guild_lead(client: Client):
            # The guild-lead card was gated on can_orient, which a guild lead has.
            _login_guild_lead(client, "qs_lead")
            grid = _tools_grid(client)
            assert "Quickstart" not in grid
            assert "/help/running-a-guild/guild-lead-quickstart/" not in grid

    def it_leaves_the_guides_reachable_in_the_help_center(client: Client):
        # Removing the tiles must not unpublish the articles behind them.
        from membership.help_content import ARTICLES

        slugs = {article["slug"] for article in ARTICLES}
        assert "instructor-quickstart" in slugs
        assert "guild-lead-quickstart" in slugs
