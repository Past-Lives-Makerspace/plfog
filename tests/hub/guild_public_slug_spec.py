"""BDD specs for the short public guild URL on the guilds surface.

``guilds.pastlives.space/woodworking/`` serves the guild slugged ``woodworking-guild``:
the redundant suffix is dropped from the public URL, an exact slug match always wins so
a collision is deterministic, and every other spelling 301s to the one canonical URL.
"""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from django.test import Client, override_settings

from membership.models import Guild
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

GUILDS_HOST = "guilds.pastlives.space"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=[GUILDS_HOST, "members.pastlives.space", "testserver"],
    GUILDS_HOSTS=[GUILDS_HOST],
    GUILDS_BASE_URL=f"https://{GUILDS_HOST}",
    MEMBER_BASE_URL="https://members.pastlives.space",
)


def describe_Guild_public_slug():
    def it_drops_a_trailing_guild_suffix(db):
        guild = GuildFactory(name="Woodworking Guild")
        assert guild.slug == "woodworking-guild"
        assert guild.public_slug == "woodworking"

    def it_leaves_a_slug_without_the_suffix_alone(db):
        guild = GuildFactory(name="Ceramics")
        assert guild.public_slug == "ceramics"

    def describe_when_the_short_slug_would_collide_with_a_reserved_path():
        def it_keeps_the_full_slug(db):
            guild = GuildFactory(name="Info Guild")
            assert guild.slug == "info-guild"
            assert guild.public_slug == "info-guild"

        def it_falls_back_to_the_long_path_when_the_slug_itself_is_reserved(db):
            guild = GuildFactory(name="Info")
            assert guild.slug == "info"
            assert guild.public_path == "/guilds/info/"

    def describe_when_both_spellings_exist():
        def it_gives_the_short_slug_to_the_guild_that_owns_it_outright(db):
            short = GuildFactory(name="Woodworking")
            suffixed = GuildFactory(name="Woodworking Guild")
            assert short.slug == "woodworking"
            assert suffixed.slug == "woodworking-guild"
            # Exact matches win, so the suffixed guild keeps its full slug in public.
            assert short.public_slug == "woodworking"
            assert suffixed.public_slug == "woodworking-guild"

        def it_resolves_the_short_slug_to_the_exact_match(db):
            short = GuildFactory(name="Woodworking")
            GuildFactory(name="Woodworking Guild")
            assert Guild.objects.get_by_public_slug("woodworking") == short

        def it_resolves_the_full_slug_to_the_suffixed_guild(db):
            GuildFactory(name="Woodworking")
            suffixed = GuildFactory(name="Woodworking Guild")
            assert Guild.objects.get_by_public_slug("woodworking-guild") == suffixed


def describe_GuildManager_get_by_public_slug():
    def it_finds_a_guild_by_its_stripped_slug(db):
        guild = GuildFactory(name="Metal Guild")
        assert Guild.objects.get_by_public_slug("metal") == guild

    def it_finds_a_guild_by_its_exact_slug(db):
        guild = GuildFactory(name="Metal Guild")
        assert Guild.objects.get_by_public_slug("metal-guild") == guild

    def it_raises_does_not_exist_for_an_unknown_slug(db):
        with pytest.raises(Guild.DoesNotExist):
            Guild.objects.get_by_public_slug("nope")

    def it_hides_a_soft_deleted_guild(db):
        guild = GuildFactory(name="Gone Guild")
        guild.soft_delete()
        with pytest.raises(Guild.DoesNotExist):
            Guild.objects.get_by_public_slug("gone")


def describe_Guild_public_url():
    @override_settings(GUILDS_BASE_URL="https://guilds.pastlives.space")
    def it_is_absolute_and_uses_the_short_slug(db):
        guild = GuildFactory(name="Fibre Guild")
        assert guild.public_path == "/fibre/"
        assert guild.public_url == "https://guilds.pastlives.space/fibre/"


def describe_the_public_guild_url():
    @pytest.fixture(autouse=True)
    def _guilds_settings():
        with override_settings(**GUILDS_SETTINGS):
            yield

    def it_serves_the_guild_page_at_the_short_root_level_slug(client: Client):
        guild = GuildFactory(name="Woodworking Guild", about="We make sawdust.")
        resp = client.get("/woodworking/", HTTP_HOST=GUILDS_HOST)
        assert resp.status_code == 200
        assert b"We make sawdust." in resp.content
        assert resp.templates[0].name == "hub/guild_detail.html"
        assert guild.slug == "woodworking-guild"

    def it_301s_the_unstripped_slug_to_the_canonical_short_one(client: Client):
        GuildFactory(name="Woodworking Guild")
        resp = client.get("/woodworking-guild/", HTTP_HOST=GUILDS_HOST)
        assert resp.status_code == 301
        assert resp["Location"] == "/woodworking/"

    def it_301s_the_long_members_style_path_to_the_canonical_short_one(client: Client):
        GuildFactory(name="Woodworking Guild")
        resp = client.get("/guilds/woodworking-guild/", HTTP_HOST=GUILDS_HOST)
        assert resp.status_code == 301
        assert resp["Location"] == "/woodworking/"

    def it_301s_an_old_numeric_link_straight_to_the_canonical_short_one(client: Client):
        guild = GuildFactory(name="Woodworking Guild")
        resp = client.get(f"/guilds/{guild.pk}/", HTTP_HOST=GUILDS_HOST)
        assert resp.status_code == 301
        assert resp["Location"] == "/woodworking/"

    def it_404s_a_root_slug_that_is_not_a_guild(rf):
        # Asserted at the middleware, not through the test client: raising Http404 from
        # SurfaceMiddleware (which runs before AuthenticationMiddleware) currently blows
        # up the error template — a pre-existing guest-surface bug fixed separately.
        from django.http import Http404

        from core.middleware import SurfaceMiddleware

        request = rf.get("/not-a-guild-at-all/", HTTP_HOST=GUILDS_HOST)
        middleware = SurfaceMiddleware(lambda _r: HttpResponse("ok"))
        with pytest.raises(Http404):
            middleware(request)

    def it_emits_one_canonical_url_on_the_page(client: Client):
        GuildFactory(name="Woodworking Guild")
        body = client.get("/woodworking/", HTTP_HOST=GUILDS_HOST).content.decode()
        assert '<link rel="canonical" href="https://guilds.pastlives.space/woodworking/">' in body
        assert 'content="https://guilds.pastlives.space/woodworking/"' in body  # og:url

    def describe_on_the_member_hub():
        def it_keeps_serving_the_long_path_without_redirecting(client: Client):
            GuildFactory(name="Woodworking Guild", about="We make sawdust.")
            resp = client.get("/guilds/woodworking-guild/", HTTP_HOST="members.pastlives.space")
            assert resp.status_code == 200
            assert b"We make sawdust." in resp.content

        def it_points_its_canonical_tag_at_the_public_guilds_url(client: Client):
            GuildFactory(name="Woodworking Guild")
            body = client.get("/guilds/woodworking-guild/", HTTP_HOST="members.pastlives.space").content.decode()
            assert '<link rel="canonical" href="https://guilds.pastlives.space/woodworking/">' in body

        def it_does_not_serve_the_short_slug(client: Client):
            GuildFactory(name="Woodworking Guild")
            assert client.get("/woodworking/", HTTP_HOST="members.pastlives.space").status_code == 404


def describe_the_public_directory():
    @pytest.fixture(autouse=True)
    def _guilds_settings():
        with override_settings(**GUILDS_SETTINGS):
            yield

    def it_links_cards_at_the_short_slug(client: Client):
        GuildFactory(name="Woodworking Guild")
        body = client.get("/guilds/", HTTP_HOST=GUILDS_HOST).content.decode()
        assert 'href="/woodworking/"' in body

    def it_links_cards_at_the_hub_path_on_the_member_host(client: Client):
        GuildFactory(name="Woodworking Guild")
        body = client.get("/guilds/", HTTP_HOST="members.pastlives.space").content.decode()
        assert 'href="/guilds/woodworking-guild/"' in body

    def it_omits_a_private_guild_from_the_public_listing(client: Client):
        GuildFactory(name="Quiet Guild", is_public=False)
        GuildFactory(name="Loud Guild")
        body = client.get("/guilds/", HTTP_HOST=GUILDS_HOST).content.decode()
        assert "Loud Guild" in body
        assert "Quiet Guild" not in body

    def it_still_lists_a_private_guild_for_members_on_the_hub(client: Client):
        GuildFactory(name="Quiet Guild", is_public=False)
        body = client.get("/guilds/", HTTP_HOST="members.pastlives.space").content.decode()
        assert "Quiet Guild" in body
