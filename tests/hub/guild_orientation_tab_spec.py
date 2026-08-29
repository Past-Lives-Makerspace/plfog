"""BDD specs for the dedicated Orientations tab on the guild detail page.

The orientation booking partial moved out of the Guild Calendar tab into its own
Orientations tab, gated on ``show_orientation``. The Guild Calendar keeps a one-line
pointer to it. Legacy deep links (``?tab=orientations`` / ``#guild-orientation``) map to
the new tab via the page's ``x-init``.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _member(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    return user


def describe_orientations_tab():
    def it_shows_the_tab_and_panel_when_orientation_is_enabled(client: Client):
        _member("ot1")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        client.login(username="ot1", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content
        # The tab button and its panel both key off section === 'orientations'.
        assert b"section === 'orientations'" in content
        assert b">Orientations</button>" in content
        # The booking partial (its anchor) lives on the page (Alpine keeps every panel in the DOM).
        assert b'id="guild-orientation"' in content

    def it_omits_the_tab_when_orientation_is_disabled(client: Client):
        _member("ot2")
        guild = GuildFactory()  # no orientation settings enabled → show_orientation False
        client.login(username="ot2", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content
        assert b"section === 'orientations'" not in content
        assert b">Orientations</button>" not in content

    def it_maps_the_deep_link_in_x_init_only_when_enabled(client: Client):
        _member("ot3")
        enabled = GuildFactory()
        GuildOrientationSettingsFactory(guild=enabled, is_enabled=True)
        disabled = GuildFactory()
        client.login(username="ot3", password="pass")
        on = client.get(reverse("hub_guild_detail", args=[enabled.slug])).content
        off = client.get(reverse("hub_guild_detail", args=[disabled.slug])).content
        assert b"t === 'orientations'" in on
        assert b"t === 'orientations'" not in off

    def it_points_from_the_calendar_to_the_orientations_tab(client: Client):
        _member("ot4")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        client.login(username="ot4", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content
        assert b"Open the Orientations tab." in content
