"""Specs for the guild page's Wiki tab: what renders, for whom, and at what query cost."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import Member, WikiPage
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    WikiPageFactory,
    WikiSearchMissFactory,
    WikiWantedPageFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    config = SiteConfiguration.load()
    config.wiki_enabled = True
    config.save()
    return config


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str, **kwargs: str) -> User:
    user = _member_user(username, **kwargs)
    client.login(username=username, password="pass")
    return user


def _guild_page(client: Client, guild) -> str:
    response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
    assert response.status_code == 200
    return response.content.decode()


def _stale(page: WikiPage) -> WikiPage:
    """Push a page past its kind's review interval by moving its whole clock back."""
    WikiPage.objects.filter(pk=page.pk).update(created_at=timezone.now() - timedelta(days=800))
    page.refresh_from_db()
    return page


def describe_the_tab_button():
    def it_renders_for_a_signed_in_member(db, client):
        _login(client, "tab_member")
        guild = GuildFactory()
        assert ">Wiki</button>" in _guild_page(client, guild)

    def it_is_absent_while_the_wiki_is_off(db, client, _wiki_on):
        _wiki_on.wiki_enabled = False
        _wiki_on.save()
        _login(client, "tab_flag_off")
        html = _guild_page(client, GuildFactory())
        assert ">Wiki</button>" not in html
        # A stray ?tab mapping on a guild with no pane would blank every pane.
        assert "t === 'wiki'" not in html

    def it_is_absent_for_an_anonymous_visitor(db, client):
        html = _guild_page(client, GuildFactory())
        assert ">Wiki</button>" not in html

    def it_is_absent_on_the_guest_guilds_surface(db, client, settings):
        """The guilds surface does not resolve wiki URLs, so a tab of links nobody can
        follow would be a dead end rather than a teaser."""
        host = settings.GUILDS_HOSTS[0]
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, host]
        _login(client, "tab_guest_surface")
        guild = GuildFactory()
        response = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=host)
        assert response.status_code == 200
        assert ">Wiki</button>" not in response.content.decode()

    def it_maps_the_tab_deep_link_only_when_the_tab_exists(db, client):
        _login(client, "tab_deeplink")
        assert "t === 'wiki'" in _guild_page(client, GuildFactory())


def describe_the_grouped_list():
    def it_orders_the_groups_and_omits_the_empty_ones(db, client):
        _login(client, "tab_groups")
        guild = GuildFactory()
        WikiPageFactory(guild=guild, kind=WikiPage.Kind.REFERENCE, title="Chart")
        WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Saw")
        html = _guild_page(client, guild)
        assert html.index("Machines") < html.index("Reference")
        assert "Materials" not in html

    def it_shows_a_see_all_link_past_twelve_rows(db, client):
        _login(client, "tab_seeall")
        guild = GuildFactory()
        for index in range(13):
            WikiPageFactory(guild=guild, kind=WikiPage.Kind.HOWTO, title=f"How to {index}")
        html = _guild_page(client, guild)
        assert "See all 13" in html
        assert f"{reverse('hub_wiki_search')}?guild={guild.slug}&amp;kind=howto" in html

    def it_leaves_another_guilds_pages_out(db, client):
        _login(client, "tab_scope")
        guild = GuildFactory()
        WikiPageFactory(guild=GuildFactory(), title="Another Guild Page")
        WikiPageFactory(guild=guild, title="This Guild Page")
        html = _guild_page(client, guild)
        assert "This Guild Page" in html
        assert "Another Guild Page" not in html

    def it_leaves_an_archived_page_out_even_for_an_admin(db, client):
        """visible_for() hands staff everything so A's tombstone can reach them by URL.
        A directory is a different question."""
        _login(client, "tab_archived_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        WikiPageFactory(guild=guild, title="Retired Guide", archived=True)
        assert "Retired Guide" not in _guild_page(client, guild)

    def describe_with_no_pages_at_all():
        def it_writes_a_real_empty_state(db, client):
            _login(client, "tab_empty")
            html = _guild_page(client, GuildFactory())
            assert "Nothing here yet." in html
            assert "+ Start A Page" in html


def describe_recently_updated():
    def it_lists_five_newest_first(db, client):
        _login(client, "tab_recent")
        guild = GuildFactory()
        for index in range(6):
            page = WikiPageFactory(guild=guild, title=f"Page {index}")
            WikiPage.objects.filter(pk=page.pk).update(updated_at=timezone.now() - timedelta(days=index))
        html = _guild_page(client, guild)
        recent = html.split("Recently Updated", 1)[1]
        assert "Page 5" not in recent.split("Wanted Pages", 1)[0]


def describe_the_lead_panels():
    def it_hides_all_three_from_a_plain_member(db, client):
        user = _login(client, "tab_panels_member")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=user.member)
        _stale(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE))
        WikiSearchMissFactory(guild=guild)
        html = _guild_page(client, guild)
        assert "Overdue For Review" not in html
        assert "Searches That Found Nothing" not in html
        # The wanted list is NOT a lead panel: every member can claim or start one.
        assert "Wanted Pages" in html

    def it_shows_all_three_to_a_lead(db, client):
        user = _login(client, "tab_panels_lead")
        guild = GuildFactory(guild_lead=user.member)
        _stale(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Old Machine"))
        WikiSearchMissFactory(guild=guild, query="epoxy cure time")
        html = _guild_page(client, guild)
        assert "Overdue For Review" in html
        assert "Searches That Found Nothing" in html
        assert "Old Machine" in html
        assert "epoxy cure time" in html

    def it_shows_them_to_an_orienter(db, client):
        user = _login(client, "tab_panels_orienter")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=user.member, role="orienter")
        assert "Overdue For Review" in _guild_page(client, guild)

    def it_keeps_a_reported_page_out_of_the_overdue_panel(db, client):
        """A page somebody has just reported belongs to spec D's queue; "Still accurate" is
        the wrong answer to "this is wrong"."""
        user = _login(client, "tab_panels_reported")
        guild = GuildFactory(guild_lead=user.member)
        reported = _stale(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Reported Machine"))
        WikiPage.objects.filter(pk=reported.pk).update(needs_review_since=timezone.now())
        html = _guild_page(client, guild)
        panel = html.split("Overdue For Review", 1)[1].split("Searches That Found Nothing", 1)[0]
        assert "Reported Machine" not in panel
        assert "Nothing overdue." in panel

    def it_writes_both_empty_states(db, client):
        user = _login(client, "tab_panels_empty")
        guild = GuildFactory(guild_lead=user.member)
        html = _guild_page(client, guild)
        assert "Nothing overdue. Everything here has been checked recently." in html
        assert "No failed searches in the last 30 days." in html


def describe_the_mobile_ordering():
    def it_comes_from_a_class_and_never_an_inline_style(db, client):
        """Rule 12: Alpine strips an inline display when x-show reveals an element, which
        is what collapsed the orientation slot table."""
        user = _login(client, "tab_order_lead")
        guild = GuildFactory(guild_lead=user.member)
        html = _guild_page(client, guild)
        assert "pl-wp-tab__grid--lead" in html
        pane = re.search(r'<div x-show="section === \'wiki\'"[^>]*>', html)
        assert pane is not None
        assert "style=" not in pane.group(0)

    def it_leaves_the_class_off_for_a_plain_member(db, client):
        _login(client, "tab_order_member")
        assert "pl-wp-tab__grid--lead" not in _guild_page(client, GuildFactory())


def describe_the_query_budget():
    def it_does_not_grow_with_the_number_of_rows(db, client):
        """The N+1 guard, stated as the thing that actually matters: a guild with 3 pages
        and a guild with 24 must cost the same. An exact-number assertion would go red on
        an improvement too, so the shape is the assertion."""
        user = _login(client, "tab_budget")
        small = GuildFactory(guild_lead=user.member)
        large = GuildFactory(guild_lead=user.member)
        for index in range(3):
            WikiPageFactory(guild=small, title=f"Small {index}")
            WikiWantedPageFactory(guild=small, title=f"Small want {index}")
        for index in range(24):
            WikiPageFactory(guild=large, title=f"Large {index}")
        for index in range(8):
            WikiWantedPageFactory(guild=large, title=f"Large want {index}")

        _guild_page(client, small)  # warm any per-process caches first
        with CaptureQueriesContext(connection) as small_ctx:
            _guild_page(client, small)
        with CaptureQueriesContext(connection) as large_ctx:
            _guild_page(client, large)
        assert len(large_ctx.captured_queries) == len(small_ctx.captured_queries)

    def it_spends_nothing_on_the_lead_panels_for_a_plain_member(db, client):
        """The lead-only work is genuinely skipped, not merely hidden in the template."""
        lead_user = _login(client, "tab_budget_lead")
        guild = GuildFactory(guild_lead=lead_user.member)
        WikiPageFactory(guild=guild)
        WikiSearchMissFactory(guild=guild)
        _guild_page(client, guild)
        with CaptureQueriesContext(connection) as lead_ctx:
            _guild_page(client, guild)
        assert any("wikisearchmiss" in query["sql"].lower() for query in lead_ctx.captured_queries)

        client.logout()
        _login(client, "tab_budget_plain")
        _guild_page(client, guild)
        with CaptureQueriesContext(connection) as member_ctx:
            _guild_page(client, guild)
        assert not any("wikisearchmiss" in query["sql"].lower() for query in member_ctx.captured_queries)
