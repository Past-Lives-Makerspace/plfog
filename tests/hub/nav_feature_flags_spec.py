"""Render specs for the Site Settings → Features nav gating in the hub sidebar."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.features import FEATURES, DEFAULT_SOON_MESSAGE
from core.models import SiteConfiguration
from membership.models import Member
from tests.features import coming_soon, hide, turn_on
from tests.membership.factories import GuildFactory, MembershipPlanFactory, UserFactory

pytestmark = pytest.mark.django_db


def _login_admin(client: Client) -> User:
    user = User.objects.create_superuser(username="navadmin", email="navadmin@x.com", password="p")
    client.login(username="navadmin", password="p")
    return user


def _login_member(client: Client, username: str = "navmember") -> User:
    """A plain ACTIVE member — the viewer a feature switch is actually aimed at.

    The switch is a member-facing UX state, so every off-state assertion below runs as this
    viewer. Running them as an admin would assert the opposite of the product and still pass
    before the reversal, which is exactly the drift these specs exist to catch.
    """
    MembershipPlanFactory()
    user = UserFactory(username=username)
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    client.force_login(user)
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
        turn_on("wiki")
        _login_admin(client)
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/wiki/" class="hub-sidebar__link' in body

    def it_hides_the_wiki_link_from_a_member_while_the_wiki_is_disabled(client: Client):
        # A member statement since #405 reversed decision 1: the wiki being off hides it from
        # members. An admin keeps the entry, which the spec below asserts.
        _login_member(client, "wiki_off_member")
        body = client.get(reverse("hub_member_directory")).content
        assert b'href="/wiki/" class="hub-sidebar__link' not in body


# ── The seven three-state feature switches (#405) ────────────────────────────────────────
#
# One nav assertion per feature per state. The sidebar is written TWICE in base.html (an
# admin block and a member block) and these run as an admin, so a gate added to only the
# member block would pass here and fail in front of members — which is why
# describe_both_sidebar_blocks below renders the member block explicitly too.

# feature key → (a substring unique to its live nav entry, its visible label)
_NAV_MARKERS: dict[str, tuple[bytes, bytes]] = {
    "meetings": (b'href="/meetings/" class="hub-sidebar__link', b"Meetings"),
    "directory": (b'href="/members/" class="hub-sidebar__link', b"Member Directory"),
    "spaces": (b'href="/spaces/" class="hub-sidebar__link', b"Spaces"),
    "equipment": (b'href="/equipment/" class="hub-sidebar__link', b"Equipment"),
    "voting": (b'href="/manage/voting/" class="hub-sidebar__link', b"Voting"),
    "wiki": (b'href="/wiki/" class="hub-sidebar__link', b"Wiki"),
}


# Voting is the one entry whose destination differs between the two sidebar blocks, so a
# member-side assertion cannot reuse the admin href (base.html: hub_admin_voting_overview vs
# hub_guild_voting). Everything else renders the same href in both.
_MEMBER_NAV_MARKERS: dict[str, tuple[bytes, bytes]] = {
    **{k: v for k, v in _NAV_MARKERS.items() if k != "voting"},
    "voting": (b'href="/guilds/voting/" class="hub-sidebar__link', b"Guild Voting"),
}


def _sidebar(client: Client) -> bytes:
    """Just the sidebar <nav>, for assertions about absence.

    The home page also carries "Your guilds" and "Latest from your guilds" CONTENT blocks,
    which amendment 3 leaves alone on purpose. Asserting over the whole body would fail on
    those and, worse, would pass for the wrong reason if the sidebar block were ever renamed.
    """
    import re

    body = client.get(reverse("hub_home")).content
    match = re.search(rb'<nav class="hub-sidebar__nav".*?</nav>', body, re.S)
    assert match is not None, "sidebar nav not found"
    return match.group(0)


def _nav(client: Client) -> bytes:
    """The home page body — a page no feature switch can 404, so it renders in every state."""
    return client.get(reverse("hub_home")).content


def describe_a_feature_that_is_on():
    def it_renders_the_live_nav_entry_for_every_feature(client: Client):
        for key in _NAV_MARKERS:
            turn_on(key)
        _login_admin(client)
        body = _sidebar(client)
        for key, (href, label) in _NAV_MARKERS.items():
            assert href in body, key
            assert label in body, key
        # Nothing is muted and no bubble exists while everything is On.
        assert b"hub-sidebar__link--soon" not in body


def describe_a_hidden_feature():
    def it_removes_the_nav_entry_entirely_for_a_member(client: Client):
        _login_member(client)
        for key, (href, _label) in _MEMBER_NAV_MARKERS.items():
            turn_on(key)
            assert href in _nav(client), f"{key} should start visible"
            hide(key)
            body = _sidebar(client)
            assert href not in body, key
            # Hidden is not Coming soon: no muted entry, no bubble, nothing at all.
            assert b"hub-sidebar__link--soon" not in body, key
            turn_on(key)


def describe_a_coming_soon_feature():
    def it_renders_a_muted_entry_that_is_not_a_link(client: Client):
        _login_member(client)
        coming_soon("voting", "Launching Sept 30th!")
        body = _sidebar(client)
        href, label = _NAV_MARKERS["voting"]
        assert href not in body  # never an <a href>
        assert b"hub-sidebar__link--soon" in body
        assert label in body  # the entry is still present and still named
        assert b'aria-disabled="true"' in body
        assert b'tabindex="0"' in body

    def it_reveals_the_admins_message_in_a_pl_help_bubble(client: Client):
        _login_member(client)
        coming_soon("spaces", "Launching Sept 30th!")
        body = _sidebar(client)
        assert b"Launching Sept 30th!" in body
        # .pl-help opens on :hover AND :focus-within, so one bubble covers mouse and keyboard.
        assert b'class="pl-help__bubble" role="tooltip"' in body
        assert b"pl-help" in body

    def it_falls_back_to_a_default_message_when_the_admin_writes_none(client: Client):
        _login_member(client)
        coming_soon("meetings", "")
        assert DEFAULT_SOON_MESSAGE.encode() in _nav(client)

    def it_never_uses_a_native_title_attribute(client: Client):
        # FRONTEND.md rule 19: a hover bubble is .pl-help, never title=, never a hand-rolled
        # popover. A native title is unreachable by keyboard, which is half of criterion 3.
        _login_member(client)
        coming_soon("wiki", "Soon!")
        body = _sidebar(client)
        soon_markup = body.split(b"hub-sidebar__link--soon")[1][:400]
        assert b"title=" not in soon_markup


def describe_both_sidebar_blocks():
    def it_gates_the_member_block_too(client: Client):
        """base.html writes the nav twice; a gate in one block does nothing for half the roster.

        The specs above render the admin block. This drives the MEMBER block, as a real member,
        and asserts the same switch still bites there.
        """
        _login_member(client, "navmember_block")
        turn_on("spaces")
        assert b'href="/spaces/" class="hub-sidebar__link' in _nav(client)
        hide("spaces")
        assert b'href="/spaces/" class="hub-sidebar__link' not in _nav(client)
        coming_soon("spaces", "Back in spring")
        body = _sidebar(client)
        assert b"hub-sidebar__link--soon" in body
        assert b"Back in spring" in body


def describe_the_guilds_section():
    """The eighth feature, and the only one whose sidebar presence is a heading plus a list.

    It also sits AFTER the admin/member branch closes in base.html, so it is written once and
    one gate covers both sidebar variants — unlike every other entry, which is written twice.
    """

    def it_shows_the_heading_and_the_guild_links_while_on(client: Client):
        _login_member(client, "guilds_on")
        GuildFactory(name="Ceramics Guild")
        turn_on("guilds")
        body = _sidebar(client)
        assert b"hubGuildsOpen" in body
        assert b"Ceramics Guild" in body

    def it_removes_the_whole_block_when_hidden(client: Client):
        _login_member(client, "guilds_hidden")
        GuildFactory(name="Ceramics Guild")
        hide("guilds")
        body = _sidebar(client)
        assert b"hubGuildsOpen" not in body  # no collapsible, no chevron
        assert b"Ceramics Guild" not in body  # no guild links
        assert b"hub-sidebar__section-toggle" not in body  # no heading

    def it_renders_an_inert_heading_with_no_list_when_coming_soon(client: Client):
        _login_member(client, "guilds_soon")
        GuildFactory(name="Ceramics Guild")
        coming_soon("guilds", "Guild pages are on their way")
        body = _sidebar(client)
        assert b"hub-sidebar__section-toggle--soon" in body
        assert b"Guild pages are on their way" in body
        # A disclosure control that discloses nothing would be worse than no control.
        assert b"hubGuildsOpen" not in body
        assert b"Ceramics Guild" not in body

    def it_keeps_the_help_key_on_the_inert_heading(client: Client):
        # nav.guilds rides the heading; the help system must still resolve it.
        _login_member(client, "guilds_helpkey")
        coming_soon("guilds", "Soon")
        assert b'data-help-key="nav.guilds"' in _sidebar(client)


def describe_the_registry():
    def it_covers_every_declared_feature(client: Client):
        """Every registry feature is asserted somewhere here, so a new one cannot ship untested.

        ``teach`` has no simple href marker (one route, two labels) and lives in
        tests/hub/teach_sidebar_spec.py; ``guilds`` is a heading plus a list, just above.
        """
        assert {f.key for f in FEATURES} == set(_NAV_MARKERS) | {"teach", "guilds"}
