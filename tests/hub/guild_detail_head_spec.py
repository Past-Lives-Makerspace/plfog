"""BDD spec for the guild page <head> on the guilds surface (MUST-FIX #8 + OG tags).

Proves the base's fonts/CSS survive the child's ``extra_head`` override (via
``{{ block.super }}``) and that per-guild Open-Graph/canonical tags are emitted,
absolute, guarded to the guilds surface, and non-empty even for a bare guild.
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

GUILDS_HOST = "guilds.pastlives.app"
GUILDS_BASE_URL = "https://guilds.pastlives.app"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=["guilds.pastlives.app", "testserver"],
    GUILDS_HOSTS=["guilds.pastlives.app"],
    GUILDS_BASE_URL=GUILDS_BASE_URL,
    BOOK_BASE_URL="https://book.pastlives.space",
    MEMBER_BASE_URL="https://members.pastlives.app",
)


def _guest_head(client: Client, guild):
    return client.get(f"/guilds/{guild.slug}/", HTTP_HOST=GUILDS_HOST).content.decode()


def describe_guild_page_head():
    @pytest.fixture(autouse=True)
    def _guilds_settings():
        with override_settings(**GUILDS_SETTINGS):
            yield

    def it_keeps_the_base_css_and_fonts_via_block_super(client: Client):
        guild = GuildFactory(name="Head Guild")
        head = _guest_head(client, guild)
        assert "cms-public.css" in head  # base aesthetic
        assert "cms-guilds.css" in head  # directory/guest extras
        assert "Playfair+Display" in head  # base fonts
        assert "calendar.css" in head  # the child's own extra_head, appended not discarding

    def it_emits_absolute_open_graph_and_canonical_tags(client: Client):
        guild = GuildFactory(name="Head Guild", about="We turn bowls and spoons.")
        head = _guest_head(client, guild)
        url = f"{GUILDS_BASE_URL}/guilds/{guild.slug}/"
        assert f'<link rel="canonical" href="{url}">' in head
        assert 'property="og:title" content="Head Guild — Past Lives Guilds"' in head
        assert "We turn bowls and spoons." in head  # og:description from about
        assert f'property="og:url" content="{url}"' in head
        assert 'property="og:image"' in head
        assert 'name="twitter:card" content="summary_large_image"' in head

    def it_falls_back_to_the_default_image_and_copy_for_a_bare_guild(client: Client):
        guild = GuildFactory(name="Bare Guild", about="")
        head = _guest_head(client, guild)
        assert "A guild at Past Lives Makerspace in Portland, OR." in head
        assert f'content="{GUILDS_BASE_URL}/static/img/og-guild-default.png"' in head

    def it_omits_open_graph_tags_on_the_members_surface(client: Client):
        guild = GuildFactory(name="Head Guild")
        head = client.get(f"/guilds/{guild.slug}/").content.decode()  # default host -> members
        assert 'property="og:title"' not in head
        assert "calendar.css" in head  # still present via the empty block.super on hub/base.html
