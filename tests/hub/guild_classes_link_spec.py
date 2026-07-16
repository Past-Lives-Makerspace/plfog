"""BDD spec for the virtual "[Guild] Classes" link injected into guild_detail.

The link is prepended to the Links card's ``links`` context so it always leads — present
even on a bare guild with zero GuildLinks and zero classes — with real GuildLink rows
following it. On the guilds surface its URL is BOOK_BASE_URL-prefixed.
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from membership.models import VirtualLink
from tests.membership.factories import GuildFactory, GuildLinkFactory

pytestmark = pytest.mark.django_db

GUILDS_HOST = "guilds.pastlives.app"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=["guilds.pastlives.app", "testserver"],
    GUILDS_HOSTS=["guilds.pastlives.app"],
    BOOK_BASE_URL="https://book.pastlives.space",
)


def describe_guild_detail_classes_link():
    def it_prepends_the_virtual_classes_link_to_links(client: Client):
        guild = GuildFactory(name="Ceramics")
        GuildLinkFactory(guild=guild, label="Discord", url="https://discord.example")

        response = client.get(f"/guilds/{guild.slug}/")

        links = response.context["links"]
        assert isinstance(links[0], VirtualLink)
        assert links[0].label == "Ceramics Classes"
        assert links[0].url == f"/classes/?guild={guild.slug}"

    def it_shows_the_link_with_zero_guildlinks_and_zero_classes(client: Client):
        # A brand-new guild: no GuildLink rows, no classes. The Links card must still
        # render because `links` is never empty, and the virtual link must appear.
        guild = GuildFactory(name="Bare Guild")

        body = client.get(f"/guilds/{guild.slug}/").content.decode()

        assert '<h3 class="hub-detail-label">Links</h3>' in body
        assert f'<a href="/classes/?guild={guild.slug}" target="_blank" rel="noopener">Bare Guild Classes</a>' in body

    def it_still_renders_real_guild_links_after_the_virtual_one(client: Client):
        guild = GuildFactory(name="Ceramics")
        GuildLinkFactory(guild=guild, label="Discord", url="https://discord.example", sort_order=0)

        response = client.get(f"/guilds/{guild.slug}/")

        links = response.context["links"]
        assert links[0].label == "Ceramics Classes"
        assert links[1].label == "Discord"

    def it_prefixes_book_base_url_on_the_guilds_surface(client: Client):
        with override_settings(**GUILDS_SETTINGS):
            guild = GuildFactory(name="Metals")
            body = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=GUILDS_HOST).content.decode()

        href = f"https://book.pastlives.space/classes/?guild={guild.slug}"
        assert f'<a href="{href}" target="_blank" rel="noopener">Metals Classes</a>' in body
