"""BDD specs for the public guild directory on the guilds surface (guilds.pastlives.app)."""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

GUILDS_HOST = "guilds.pastlives.app"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=["guilds.pastlives.app", "members.pastlives.space", "book.pastlives.space", "testserver"],
    GUILDS_HOSTS=["guilds.pastlives.app"],
    GUILDS_BASE_URL="https://guilds.pastlives.app",
    MEMBER_BASE_URL="https://members.pastlives.app",
    BOOK_BASE_URL="https://book.pastlives.space",
)


def _get(client: Client, path: str = "/guilds/"):
    return client.get(path, HTTP_HOST=GUILDS_HOST)


def describe_guild_directory():
    @pytest.fixture(autouse=True)
    def _guilds_settings():
        with override_settings(**GUILDS_SETTINGS):
            yield

    def it_renders_200_for_anonymous_guests(client: Client):
        GuildFactory(name="Woodworking")
        assert _get(client).status_code == 200

    def it_lists_active_guilds(client: Client):
        GuildFactory(name="Ceramics Guild")
        assert b"Ceramics Guild" in _get(client).content

    def it_orders_featured_first_then_alphabetical(client: Client):
        GuildFactory(name="Zebra Guild")
        GuildFactory(name="Alpha Guild")
        GuildFactory(name="Featured Guild", is_featured=True)
        body = _get(client).content.decode()
        assert body.index("Featured Guild") < body.index("Alpha Guild") < body.index("Zebra Guild")

    def it_excludes_inactive_and_soft_deleted_guilds(client: Client):
        GuildFactory(name="Inactive Guild", is_active=False)
        gone = GuildFactory(name="Deleted Guild")
        gone.soft_delete()
        body = _get(client).content
        assert b"Inactive Guild" not in body
        assert b"Deleted Guild" not in body

    def it_shows_the_member_count_never_member_names(client: Client):
        # Count-only: a member who opted into the directory must not have their
        # name rendered on the public listing (only the numeric count chip).
        MembershipPlanFactory()
        guild = GuildFactory(name="Roster Guild", show_members=True)
        member = MemberFactory(full_legal_name="Prudence Privatename", show_in_directory=True)
        from membership.models import GuildMembership

        GuildMembership.objects.create(guild=guild, member=member)
        body = _get(client).content
        assert b"1 member" in body
        assert b"Prudence Privatename" not in body

    def it_shows_an_empty_state_when_no_guilds(client: Client):
        assert b"No guilds are listed yet" in _get(client).content

    def it_renders_guest_chrome_not_the_member_sidebar(client: Client):
        GuildFactory(name="Chrome Guild")
        body = _get(client).content
        assert b"hub-sidebar" not in body
        assert b"cp-topbar" in body
