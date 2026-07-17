"""BDD spec for Guild.classes_link — the virtual "[Guild] Classes" link.

The link is computed (never a stored GuildLink): its label reflects the current name and
its URL points at the public class catalog pre-filtered to this guild via ?guild=<slug>,
root-relative on the members surface and BOOK_BASE_URL-prefixed on the guilds surface.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import override_settings

from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def describe_Guild_classes_link():
    def it_labels_the_link_with_the_guild_name_plus_classes():
        guild = GuildFactory(name="Ceramics")
        assert guild.classes_link().label == "Ceramics Classes"

    def it_builds_a_root_relative_url_on_the_members_surface():
        guild = GuildFactory(name="Woodshop", slug="woodshop")
        assert guild.classes_link().url == "/classes/?guild=woodshop"

    def it_defaults_to_the_members_surface():
        guild = GuildFactory(slug="glass")
        # No guilds_surface argument → same as guilds_surface=False (root-relative).
        assert guild.classes_link().url == guild.classes_link(guilds_surface=False).url

    @override_settings(BOOK_BASE_URL="https://book.pastlives.space")
    def it_prefixes_book_base_url_on_the_guilds_surface():
        guild = GuildFactory(name="Metals", slug="metals")
        url = guild.classes_link(guilds_surface=True).url
        assert url.startswith(settings.BOOK_BASE_URL)
        assert url == "https://book.pastlives.space/classes/?guild=metals"

    def it_reflects_a_renamed_guild():
        guild = GuildFactory(name="Old Name")
        assert guild.classes_link().label == "Old Name Classes"
        guild.name = "New Name"
        # Recomputed each call — no stored copy to drift.
        assert guild.classes_link().label == "New Name Classes"
