"""BDD specs for hidden guilds in the sidebar and on their own page.

A hidden guild (``is_active=False``) is private to admins and that guild's lead and staff: they
find it in the sidebar, tagged Hidden, and open its page; every other member gets neither. The
Help Center's example guild keeps its direct link for everyone and stays off the sidebar.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from hub.view_as import ROLE_MEMBER, ViewAs, compute_actual_roles
from membership.models import EXAMPLE_GUILD_SLUG, GuildStaffMembership, Member
from membership.permissions import can_view_guild, sidebar_guilds
from tests.features import turn_on
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    UserFactory,
)

TAG = 'class="hub-sidebar__private"'


@pytest.fixture
def plain_user(db):
    MembershipPlanFactory()
    return UserFactory(username="hidden-guild-member@example.com")


@pytest.fixture
def admin_user(db):
    MembershipPlanFactory()
    user = UserFactory(username="hidden-guild-admin@example.com")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _request(rf, user, picked: str | None = None):
    request = rf.get("/")
    request.user = user
    request.view_as = ViewAs(compute_actual_roles(user), picked) if user.is_authenticated else None
    return request


def _sidebar(client) -> str:
    turn_on("guilds")
    html = client.get(reverse("hub_home")).content.decode()
    start = html.index('aria-label="Hub navigation"')
    return html[start : html.index("</nav>", start)]


def describe_sidebar_guilds():
    def it_gives_an_admin_every_hidden_guild_but_the_example(admin_user, rf):
        GuildFactory(name="Open Guild")
        GuildFactory(name="Music Guild", is_active=False)
        GuildFactory(name="Cartographers Guild", slug=EXAMPLE_GUILD_SLUG, is_active=False)
        names = [g.name for g in sidebar_guilds(_request(rf, admin_user))]
        assert names == ["Music Guild", "Open Guild"]

    def it_lists_the_example_guild_once_while_the_demo_setting_is_on(admin_user, rf):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.display_demo_guild = True
        config.save()
        GuildFactory(name="Cartographers Guild", slug=EXAMPLE_GUILD_SLUG, is_active=False)
        GuildFactory(name="Music Guild", is_active=False)
        names = [g.name for g in sidebar_guilds(_request(rf, admin_user))]
        assert names == ["Cartographers Guild", "Music Guild"]

    def it_drops_hidden_guilds_while_an_admin_previews_as_a_member(admin_user, rf):
        GuildFactory(name="Open Guild")
        GuildFactory(name="Music Guild", is_active=False)
        names = [g.name for g in sidebar_guilds(_request(rf, admin_user, ROLE_MEMBER))]
        assert names == ["Open Guild"]

    def it_gives_a_lead_and_a_staffer_only_their_own_hidden_guild(plain_user, rf):
        member = plain_user.member
        GuildFactory(name="Led Guild", guild_lead=member, is_active=False)
        staffed = GuildFactory(name="Staffed Guild", is_active=False)
        GuildStaffMembershipFactory(member=member, guild=staffed, role=GuildStaffMembership.Role.ORIENTER)
        GuildFactory(name="Someone Else's Guild", is_active=False)
        names = [g.name for g in sidebar_guilds(_request(rf, plain_user))]
        assert names == ["Led Guild", "Staffed Guild"]

    def it_gives_a_member_no_hidden_guild(plain_user, rf):
        GuildFactory(name="Open Guild")
        GuildFactory(name="Music Guild", is_active=False)
        assert [g.name for g in sidebar_guilds(_request(rf, plain_user))] == ["Open Guild"]

    def it_gives_a_viewer_without_a_member_the_active_guilds(db, rf):
        GuildFactory(name="Open Guild")
        GuildFactory(name="Music Guild", is_active=False)
        assert [g.name for g in sidebar_guilds(_request(rf, AnonymousUser()))] == ["Open Guild"]

    def it_runs_one_query(plain_user, rf):
        GuildFactory(name="Led Guild", guild_lead=plain_user.member, is_active=False)
        request = _request(rf, plain_user)
        with CaptureQueriesContext(connection) as queries:
            list(sidebar_guilds(request))
        assert len(queries) == 1


def describe_the_sidebar():
    def it_tags_a_hidden_guild_for_its_admin(admin_user, client):
        GuildFactory(name="Music Guild", is_active=False)
        GuildFactory(name="Open Guild")
        client.force_login(admin_user)
        nav = _sidebar(client)
        music = nav[nav.index("Music Guild") : nav.index("</a>", nav.index("Music Guild"))]
        open_ = nav[nav.index("Open Guild") : nav.index("</a>", nav.index("Open Guild"))]
        assert TAG in music
        assert TAG not in open_

    def it_leaves_a_hidden_guild_out_for_a_member(plain_user, client):
        GuildFactory(name="Music Guild", is_active=False)
        GuildFactory(name="Open Guild")
        client.force_login(plain_user)
        nav = _sidebar(client)
        assert "Open Guild" in nav
        assert "Music Guild" not in nav


def describe_can_view_guild():
    def it_opens_an_active_guild_to_anyone(db, rf):
        assert can_view_guild(_request(rf, AnonymousUser()), GuildFactory())

    def it_opens_the_example_guild_to_anyone(db, rf):
        guild = GuildFactory(name="Cartographers Guild", slug=EXAMPLE_GUILD_SLUG, is_active=False)
        assert can_view_guild(_request(rf, AnonymousUser()), guild)

    def it_opens_a_hidden_guild_to_an_admin(admin_user, rf):
        assert can_view_guild(_request(rf, admin_user), GuildFactory(is_active=False))

    def it_opens_a_hidden_guild_to_its_staff(plain_user, rf):
        guild = GuildFactory(is_active=False)
        GuildStaffMembershipFactory(member=plain_user.member, guild=guild, role=GuildStaffMembership.Role.ORIENTER)
        assert can_view_guild(_request(rf, plain_user), guild)

    def it_closes_a_hidden_guild_to_a_member_and_to_a_guest(plain_user, rf):
        guild = GuildFactory(is_active=False)
        assert not can_view_guild(_request(rf, plain_user), guild)
        assert not can_view_guild(_request(rf, AnonymousUser()), guild)


def describe_the_guild_page():
    def it_is_a_404_for_a_member_on_a_hidden_guild(plain_user, client):
        guild = GuildFactory(name="Music Guild", is_active=False)
        client.force_login(plain_user)
        assert client.get(reverse("hub_guild_detail", args=[guild.slug])).status_code == 404

    def it_opens_for_the_hidden_guilds_lead(plain_user, client):
        guild = GuildFactory(name="Music Guild", guild_lead=plain_user.member, is_active=False)
        client.force_login(plain_user)
        assert client.get(reverse("hub_guild_detail", args=[guild.slug])).status_code == 200

    def it_opens_for_an_admin(admin_user, client):
        guild = GuildFactory(name="Music Guild", is_active=False)
        client.force_login(admin_user)
        assert client.get(reverse("hub_guild_detail", args=[guild.slug])).status_code == 200
