"""#467, part 2: the "Get the app" store badges on the web surfaces.

One partial, ``templates/includes/app_store_badges.html``, renders on the members host sign in
page (both templates), the member home, the hub sidebar tray and the logged out landing page.
It renders with the ``hidden`` attribute and ``static/js/app-store-badges.js`` reveals it in a
browser, so the native app never flashes a "download the app" prompt. The store URLs come from
the ``brand()`` context processor; a blank App Store URL leaves that badge out and the caption
says iOS is coming soon.

Assertions anchor on the partial's markup (``data-pl-app-badges``, its classes, the alt text)
and on the caption element, never on bare copy, because the changelog renders on every page
(STANDARDS §8).
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

PLAY = "https://play.google.com/store/apps/details?id=app.pastlives.hub"  # the model default
IOS = "https://apps.apple.com/app/id1234567890"

PLAY_ALT = 'alt="Get it on Google Play"'
IOS_ALT = 'alt="Download on the App Store"'

GUILDS_HOST = "guilds.pastlives.app"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=["guilds.pastlives.app", "testserver"],
    GUILDS_HOSTS=["guilds.pastlives.app"],
    GUILDS_BASE_URL="https://guilds.pastlives.app",
    BOOK_BASE_URL="https://book.pastlives.space",
    MEMBER_BASE_URL="https://members.pastlives.app",
)

# The opening tag of every badge block the partial rendered.
_BLOCK_RE = re.compile(r"<div class=\"[^\"]*\bpl-app-badges\b[^\"]*\"[^>]*>")
_CAPTION_RE = re.compile(r'<p class="pl-app-badges__caption">([^<]*)</p>')
_BADGE_LINK_RE = re.compile(r"<a [^>]*class=\"pl-app-badges__link\"[^>]*>")


def _set_stores(*, play: str = PLAY, ios: str = "") -> None:
    config = SiteConfiguration.load()
    config.google_play_url = play
    config.app_store_url = ios
    config.save()


def _blocks(html: str) -> list[str]:
    return _BLOCK_RE.findall(html)


def _captions(html: str) -> list[str]:
    return _CAPTION_RE.findall(html)


def _badge_links(html: str) -> list[str]:
    return _BADGE_LINK_RE.findall(html)


def _card(html: str) -> str:
    """The home card's opening tag; a hub page also carries the sidebar tray block."""
    (card,) = [block for block in _blocks(html) if "hub-card" in block]
    return card


def _member_client(username: str = "badges-member") -> Client:
    MembershipPlanFactory()
    User.objects.create_user(username=username, password="pass")
    client = Client()
    client.login(username=username, password="pass")
    return client


def _unlinked_client() -> Client:
    user = User.objects.create_user(username="badges-orphan", password="pass")
    Member.objects.filter(user=user).delete()
    client = Client()
    client.login(username="badges-orphan", password="pass")
    return client


def describe_the_badge_block():
    def it_renders_hidden_until_the_script_reveals_it(client: Client):
        html = client.get(reverse("account_login")).content.decode()
        (block,) = _blocks(html)
        assert "data-pl-app-badges" in block
        assert " hidden" in block

    def it_links_each_official_badge_to_its_store_in_a_new_tab(client: Client):
        _set_stores(play=PLAY, ios=IOS)
        html = client.get(reverse("account_login")).content.decode()
        links = _badge_links(html)
        assert [f'href="{PLAY}"' in link for link in links] == [True, False]
        assert [f'href="{IOS}"' in link for link in links] == [False, True]
        for link in links:
            assert 'target="_blank"' in link
            assert 'rel="noopener"' in link
            assert 'hx-boost="false"' in link
        assert PLAY_ALT in html
        assert IOS_ALT in html
        # Static storage hashes filenames in production, so match the stems.
        assert "img/google-play-badge" in html
        assert "img/app-store-badge" in html

    def it_loads_the_reveal_script_once_on_a_public_page(client: Client):
        html = client.get(reverse("account_login")).content.decode()
        assert html.count("js/app-store-badges.js") == 1

    def describe_while_ios_is_unlaunched():
        def it_shows_only_the_play_badge_and_says_ios_is_coming_soon(client: Client):
            _set_stores(play=PLAY, ios="")
            html = client.get(reverse("account_login")).content.decode()
            assert len(_badge_links(html)) == 1
            assert PLAY_ALT in html
            assert IOS_ALT not in html
            assert _captions(html) == ["Past Lives on your phone. Get it on Google Play; iOS coming soon."]

    def describe_when_the_app_store_url_is_set():
        def it_shows_both_badges_and_drops_the_coming_soon_wording(client: Client):
            _set_stores(play=PLAY, ios=IOS)
            html = client.get(reverse("account_login")).content.decode()
            assert len(_badge_links(html)) == 2
            assert _captions(html) == ["Past Lives on your phone. Download for iPhone or Android."]

    def describe_when_only_the_app_store_is_set():
        def it_shows_only_the_apple_badge(client: Client):
            _set_stores(play="", ios=IOS)
            html = client.get(reverse("account_login")).content.decode()
            assert len(_badge_links(html)) == 1
            assert IOS_ALT in html
            assert PLAY_ALT not in html
            assert _captions(html) == ["Past Lives on your phone. Download on the App Store."]

    def describe_when_no_store_is_set():
        def it_renders_no_block_at_all(client: Client):
            _set_stores(play="", ios="")
            assert _blocks(client.get(reverse("account_login")).content.decode()) == []
            assert _blocks(client.get(reverse("home")).content.decode()) == []
            assert _blocks(_member_client().get(reverse("hub_home")).content.decode()) == []

    def it_names_the_configured_short_name_in_the_caption(client: Client):
        config = SiteConfiguration.load()
        config.org_short_name = "Fletcher"
        config.save()
        html = client.get(reverse("account_login")).content.decode()
        assert _captions(html) == ["Fletcher on your phone. Get it on Google Play; iOS coming soon."]


def describe_the_login_page():
    def it_places_the_block_beneath_the_sign_in_card_on_the_members_host(client: Client):
        html = client.get(reverse("account_login")).content.decode()
        (block,) = _blocks(html)
        assert html.index('class="auth-links"') < html.index(block) < html.index("</main>")

    def it_places_the_block_on_the_request_login_code_page_too(client: Client):
        html = client.get(reverse("account_request_login_code")).content.decode()
        (block,) = _blocks(html)
        assert 'class="auth-card"' in html
        assert html.index('class="auth-links"') < html.index(block) < html.index("</main>")

    def it_leaves_the_guest_surface_variant_unchanged():
        with override_settings(**GUILDS_SETTINGS):
            for url_name in ("account_login", "account_request_login_code"):
                html = Client().get(reverse(url_name), HTTP_HOST=GUILDS_HOST).content.decode()
                assert "bk-auth-card" in html
                assert _blocks(html) == []


def describe_the_member_home():
    def it_shows_the_get_the_app_card_after_your_guilds_for_a_linked_member():
        html = _member_client().get(reverse("hub_home")).content.decode()
        card = _card(html)
        assert "pl-home-block" in card
        assert " hidden" in card
        heading = '<h2 class="pl-home-heading">Get The App</h2>'
        assert heading in html
        assert html.index("Your Guilds</h2>") < html.index(card) < html.index(heading)

    def it_shows_the_card_for_an_unlinked_account_too():
        html = _unlinked_client().get(reverse("hub_home")).content.decode()
        card = _card(html)
        assert html.index("isn't linked to a membership") < html.index(card)
        assert '<h2 class="pl-home-heading">Get The App</h2>' in html

    def it_carries_the_caption_on_the_card():
        html = _member_client().get(reverse("hub_home")).content.decode()
        assert _captions(html) == ["Past Lives on your phone. Get it on Google Play; iOS coming soon."]


def describe_the_hub_sidebar_tray():
    @pytest.fixture
    def member(db):
        return _member_client()

    def it_renders_a_hidden_tray_row_under_the_website_and_discord_icons(member: Client):
        html = member.get(reverse("hub_home")).content.decode()
        trays = [block for block in _blocks(html) if "pl-app-badges--tray" in block]
        (tray,) = trays
        assert " hidden" in tray
        assert html.index('class="hub-sidebar__icons"') < html.index(tray) < html.index("hub-sidebar__link--feedback")

    def it_never_boosts_a_store_link(member: Client):
        html = member.get(reverse("hub_home")).content.decode()
        tray_start = html.index("pl-app-badges--tray")
        tray_html = html[tray_start : html.index("hub-sidebar__link--feedback")]
        links = _badge_links(tray_html)
        assert links, "no badge link inside the tray"
        for link in links:
            assert 'hx-boost="false"' in link
            assert 'target="_blank"' in link

    def it_has_no_caption_in_the_tray(member: Client):
        html = member.get(reverse("hub_home")).content.decode()
        tray_start = html.index("pl-app-badges--tray")
        tray_html = html[tray_start : html.index("hub-sidebar__link--feedback")]
        assert "pl-app-badges__caption" not in tray_html

    def it_appears_on_other_hub_pages_as_well(member: Client):
        for url_name in ("hub_user_settings", "hub_community_calendar", "hub_help"):
            html = member.get(reverse(url_name)).content.decode()
            assert any("pl-app-badges--tray" in block for block in _blocks(html)), url_name

    def it_loads_the_reveal_script_from_the_head_once(member: Client):
        html = member.get(reverse("hub_home")).content.decode()
        head, _sep, body = html.partition("<body")
        assert head.count("js/app-store-badges.js") == 1
        assert "app-store-badges.js" not in body


def describe_the_landing_page():
    def it_places_the_block_after_the_hero_actions(client: Client):
        html = client.get(reverse("home")).content.decode()
        (block,) = _blocks(html)
        assert html.index('class="hero__actions"') < html.index(block) < html.index("</section>")

    def it_loads_the_reveal_script_once(client: Client):
        html = client.get(reverse("home")).content.decode()
        assert html.count("js/app-store-badges.js") == 1
