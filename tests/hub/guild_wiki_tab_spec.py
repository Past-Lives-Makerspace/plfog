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

        def it_hides_the_search_box(db, client):
            # Searching this guild's wiki over zero pages can only disappoint, and the row
            # put a second "+ Start A Page" a couple of inches above the card's own.
            _login(client, "tab_empty_search")
            html = _guild_page(client, GuildFactory(name="Gardeners Guild", slug="gardeners"))
            assert "Search the Gardeners Guild wiki" not in html
            assert html.count("+ Start A Page") == 1

        def it_keeps_a_way_through_to_the_whole_wiki(db, client):
            # The row it replaces also offered an "Everything" scope, so the cross-guild
            # search must not disappear along with it.
            _login(client, "tab_empty_browse")
            html = _guild_page(client, GuildFactory())
            assert "Browse the whole wiki" in html
            assert reverse("hub_wiki_home") in html

        def it_brings_the_search_box_back_with_the_first_page(db, client):
            _login(client, "tab_search_back")
            guild = GuildFactory(name="Gardeners Guild", slug="gardeners-two")
            WikiPageFactory(guild=guild, title="Something Written")
            html = _guild_page(client, guild)
            assert "Search the Gardeners Guild wiki" in html


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
        # The wanted list is NOT a lead panel — but with nothing on it there is nothing to
        # show a member either, and the card used to render anyway saying "No requests yet."
        assert "Wanted Pages" not in html

    def it_shows_the_wanted_card_to_a_plain_member_once_something_is_on_it(db, client):
        user = _login(client, "tab_panels_member_wanted")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=user.member)
        WikiWantedPageFactory(guild=guild, title="Bandsaw Blade Change")
        html = _guild_page(client, guild)
        assert "Wanted Pages" in html
        assert "Bandsaw Blade Change" in html

    def it_shows_all_three_to_a_lead(db, client):
        user = _login(client, "tab_panels_lead")
        guild = GuildFactory(guild_lead=user.member)
        _stale(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Old Machine"))
        WikiSearchMissFactory(guild=guild, query="epoxy cure time")
        WikiWantedPageFactory(guild=guild, title="Bandsaw Blade Change")
        html = _guild_page(client, guild)
        assert "Overdue For Review" in html
        assert "Searches That Found Nothing" in html
        assert "Wanted Pages" in html
        assert "Old Machine" in html
        assert "epoxy cure time" in html

    def it_shows_them_to_an_orienter(db, client):
        user = _login(client, "tab_panels_orienter")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=user.member, role="orienter")
        _stale(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE))
        assert "Overdue For Review" in _guild_page(client, guild)

    def it_keeps_a_reported_page_out_of_the_overdue_panel(db, client):
        """A page somebody has just reported belongs to spec D's queue; "Still accurate" is
        the wrong answer to "this is wrong"."""
        user = _login(client, "tab_panels_reported")
        guild = GuildFactory(guild_lead=user.member)
        # A second stale page nobody reported, so the panel is on the screen at all: the
        # panel is gated on having rows now, and "the reported page is not in it" would
        # pass against a panel that never rendered.
        _stale(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Fine Machine"))
        reported = _stale(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Reported Machine"))
        WikiPage.objects.filter(pk=reported.pk).update(needs_review_since=timezone.now())
        html = _guild_page(client, guild)
        panel = html.split("Overdue For Review", 1)[1].split('id="wiki-confirm-cue"', 1)[0]
        assert "Fine Machine" in panel
        assert "Reported Machine" not in panel

    def describe_when_a_panel_has_nothing_in_it():
        def it_renders_no_panel_and_no_empty_state(db, client):
            # Three cards saying "Nothing overdue", "No failed searches" and "No requests
            # yet" is a column that describes curation machinery for content nobody has
            # written. On a wiki that launched empty it was most of the guild tab.
            user = _login(client, "tab_panels_empty")
            guild = GuildFactory(guild_lead=user.member)
            html = _guild_page(client, guild)
            assert "Overdue For Review" not in html
            assert "Searches That Found Nothing" not in html
            assert "Wanted Pages" not in html
            assert "Nothing overdue." not in html
            assert "No failed searches in the last 30 days." not in html
            assert "No requests yet." not in html

        def it_drops_the_whole_column_and_widens_the_list(db, client):
            user = _login(client, "tab_panels_solo")
            guild = GuildFactory(guild_lead=user.member)
            html = _guild_page(client, guild)
            assert "pl-wp-tab__col--panels" not in html
            assert "pl-wp-tab__grid--solo" in html

        def it_keeps_the_column_as_soon_as_one_card_has_rows(db, client):
            user = _login(client, "tab_panels_notsolo")
            guild = GuildFactory(guild_lead=user.member)
            WikiPageFactory(guild=guild, title="Something Written")
            html = _guild_page(client, guild)
            assert "pl-wp-tab__col--panels" in html
            assert "pl-wp-tab__grid--solo" not in html

        def it_leaves_a_lead_one_line_to_the_wanted_list(db, client):
            # The card is gone, the entry point is not: a lead with pages but no requests
            # still needs somewhere to file one from.
            user = _login(client, "tab_panels_leadlink")
            guild = GuildFactory(guild_lead=user.member)
            WikiPageFactory(guild=guild, title="Something Written")
            html = _guild_page(client, guild)
            assert "Wanted Pages" not in html
            assert "Ask for a page someone should write" in html

        def it_offers_a_plain_member_no_such_line(db, client):
            user = _login(client, "tab_panels_memberlink")
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=user.member)
            WikiPageFactory(guild=guild, title="Something Written")
            assert "Ask for a page someone should write" not in _guild_page(client, guild)

    def it_keeps_the_wanted_card_in_the_dom_for_the_failed_search_swap(db, client):
        """ "Add To Wanted" in the failed-search panel swaps the new row into
        #wiki-tab-wanted-rows out of band. With the card gated on already having rows that
        target would be missing exactly when a lead first uses the button, htmx would drop
        the swap, and the request would land in the database with nothing on screen to say
        so."""
        user = _login(client, "tab_panels_oobtarget")
        guild = GuildFactory(guild_lead=user.member)
        WikiSearchMissFactory(guild=guild, query="epoxy cure time")
        html = _guild_page(client, guild)
        assert "Searches That Found Nothing" in html
        assert 'id="wiki-tab-wanted-rows"' in html


def describe_the_compact_verify_button():
    def it_is_absent_on_a_page_carrying_an_open_report(db, client):
        """verify() clears the denormalized needs-review pair, so a one-tap Verify beside
        an amber pill would let a lead make the banner vanish from a list without ever
        opening the page or reading what somebody said was wrong. A reported page is spec
        D's surface; the lead follows the title and verifies from there."""
        user = _login(client, "tab_verify_reported")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild, title="Reported Saw")
        WikiPage.objects.filter(pk=page.pk).update(needs_review_since=timezone.now())
        html = _guild_page(client, guild)
        assert "Reported Saw" in html
        assert 'name="surface" value="tab"' not in html

    def it_is_present_on_the_same_page_once_nothing_is_reported(db, client):
        user = _login(client, "tab_verify_unreported")
        guild = GuildFactory(guild_lead=user.member)
        WikiPageFactory(guild=guild, title="Quiet Saw")
        assert 'name="surface" value="tab"' in _guild_page(client, guild)


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
