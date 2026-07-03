"""BDD specs for the public vanity redirect: pastlives.app/g/<slug> → guest guild page.

The vanity route is public (no @login_required), reachable on the member host pre-login,
and 301-redirects to the guest guild page on GUILDS_BASE_URL. Unknown or soft-deleted
slugs 404 (the default Guild manager hides soft-deleted guilds).
"""

from __future__ import annotations

import pytest
from django.test import Client, override_settings
from django.urls import resolve, reverse

from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

VANITY_SETTINGS = dict(
    MEMBER_BASE_URL="https://pastlives.app",
    GUILDS_BASE_URL="https://guilds.pastlives.app",
)


def describe_guild_vanity_redirect():
    @pytest.fixture(autouse=True)
    def _settings():
        with override_settings(**VANITY_SETTINGS):
            yield

    def it_permanently_redirects_an_anonymous_visitor_to_the_guest_page(client: Client):
        guild = GuildFactory(name="Ceramics")
        resp = client.get(f"/g/{guild.slug}/")
        assert resp.status_code == 301
        assert resp["Location"] == f"https://guilds.pastlives.app/guilds/{guild.slug}/"

    def it_reaches_the_view_without_login_on_the_member_host(client: Client):
        # Proves the view carries no @login_required — an anonymous GET yields the
        # 301 (not a bounce to /accounts/login/).
        guild = GuildFactory(name="Woodworking")
        resp = client.get(f"/g/{guild.slug}/")
        assert resp.status_code == 301
        assert "/accounts/login/" not in resp["Location"]

    def it_resolves_to_the_named_vanity_route(client: Client):
        guild = GuildFactory(name="Fiber Arts")
        match = resolve(f"/g/{guild.slug}/")
        assert match.url_name == "guild_vanity"
        assert reverse("guild_vanity", args=[guild.slug]) == f"/g/{guild.slug}/"

    def it_404s_for_an_unknown_slug(client: Client):
        assert client.get("/g/does-not-exist/").status_code == 404

    def it_404s_for_a_soft_deleted_guild(client: Client):
        guild = GuildFactory(name="Retired Guild")
        slug = guild.slug
        guild.soft_delete()
        assert client.get(f"/g/{slug}/").status_code == 404

    def it_404s_for_a_private_guild(client: Client):
        # A private guild's page is 404 on the guest surface, so we never publicly
        # redirect to it — the vanity link would only dead-end.
        guild = GuildFactory(name="Hidden Guild", is_public=False)
        assert client.get(f"/g/{guild.slug}/").status_code == 404
