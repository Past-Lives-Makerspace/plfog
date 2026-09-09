"""Specs for Searches That Found Nothing, and the "Add To Wanted" that must not lie."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member, WikiWantedPage
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    WikiPageFactory,
    WikiSearchMissFactory,
    WikiWantedPageFactory,
)

pytestmark = pytest.mark.django_db

_HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def _wiki_on(db):
    config = SiteConfiguration.load()
    config.wiki_enabled = True
    config.save()
    return config


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _panel(client: Client, guild) -> str:
    html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
    if "Searches That Found Nothing" not in html:
        return ""
    return html.split("Searches That Found Nothing", 1)[1].split("Wanted Pages", 1)[0]


def describe_the_panel():
    def it_shows_the_members_own_words_and_counts_people(db, client):
        user = _login(client, "miss_panel_lead")
        guild = GuildFactory(guild_lead=user.member)
        WikiSearchMissFactory(guild=guild, query="epoxy cure time", member=MemberFactory())
        WikiSearchMissFactory(guild=guild, query="Epoxy Cure Time", member=MemberFactory())
        panel = _panel(client, guild)
        assert "epoxy cure time" in panel.lower()
        assert "2 people" in panel

    def it_offers_add_to_wanted_when_nothing_matches(db, client):
        user = _login(client, "miss_panel_button")
        guild = GuildFactory(guild_lead=user.member)
        WikiSearchMissFactory(guild=guild, query="epoxy cure time")
        panel = _panel(client, guild)
        assert "Add To Wanted" in panel
        assert 'name="bump" value="0"' in panel

    def it_replaces_the_button_when_an_open_row_already_matches(db, client):
        """An actioned row that still shows a live button is a control that lies: a second
        tap re-posts, and the panel's headline number counts PEOPLE."""
        user = _login(client, "miss_panel_onlist")
        guild = GuildFactory(guild_lead=user.member)
        WikiSearchMissFactory(guild=guild, query="Epoxy Cure Time")
        WikiWantedPageFactory(guild=guild, title="epoxy cure time")
        panel = _panel(client, guild)
        assert "On the wanted list" in panel
        assert "Add To Wanted" not in panel

    def it_offers_the_button_again_once_the_row_is_written(db, client):
        """A written page means the ask is live again if members are still missing it."""
        user = _login(client, "miss_panel_written")
        guild = GuildFactory(guild_lead=user.member)
        WikiSearchMissFactory(guild=guild, query="epoxy cure time")
        row = WikiWantedPageFactory(guild=guild, title="epoxy cure time")
        row.fulfil(WikiPageFactory(guild=guild))
        panel = _panel(client, guild)
        assert "Add To Wanted" in panel

    def it_offers_a_start_this_page_link_carrying_the_query(db, client):
        user = _login(client, "miss_panel_start")
        guild = GuildFactory(guild_lead=user.member)
        WikiSearchMissFactory(guild=guild, query="epoxy cure time")
        panel = _panel(client, guild)
        assert f"guild={guild.slug}&amp;title=epoxy%20cure%20time" in panel


def describe_add_to_wanted():
    def it_does_not_increment_the_people_count(db, client):
        """A lead filing a row is not a fourth person asking."""
        user = _login(client, "miss_add_nobump")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild, title="Epoxy cure time")
        WikiWantedPage.objects.filter(pk=row.pk).update(request_count=3)
        response = client.post(
            reverse("hub_wiki_wanted_request"),
            {"title": "epoxy cure time", "guild": guild.slug, "bump": "0", "surface": "panel"},
            **_HTMX,
        )
        assert response.status_code == 200
        row.refresh_from_db()
        assert row.request_count == 3

    def it_swaps_the_row_and_refreshes_the_wanted_card(db, client):
        user = _login(client, "miss_add_swap")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(
            reverse("hub_wiki_wanted_request"),
            {"title": "epoxy cure time", "guild": guild.slug, "bump": "0", "surface": "panel"},
            **_HTMX,
        )
        body = response.content.decode()
        assert "On the wanted list" in body
        assert 'id="wiki-tab-wanted-rows"' in body
        assert 'hx-swap-oob="true"' in body
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "Added to Wanted pages."

    def it_refuses_a_plain_member_suppressing_the_count(db, client):
        _login(client, "miss_add_plain")
        guild = GuildFactory()
        response = client.post(
            reverse("hub_wiki_wanted_request"),
            {"title": "epoxy cure time", "guild": guild.slug, "bump": "0", "surface": "panel"},
            **_HTMX,
        )
        assert response.status_code == 403

    def it_refuses_a_title_that_is_not_really_an_ask(db, client):
        user = _login(client, "miss_add_short")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(
            reverse("hub_wiki_wanted_request"),
            {"title": "ab", "guild": guild.slug, "bump": "0"},
            **_HTMX,
        )
        assert response.status_code == 400

    def it_refuses_a_member_whose_membership_lapsed(db, client):
        user = _login(client, "miss_add_lapsed")
        guild = GuildFactory(guild_lead=user.member)
        user.member.status = Member.Status.FORMER
        user.member.save(update_fields=["status"])
        response = client.post(
            reverse("hub_wiki_wanted_request"),
            {"title": "epoxy cure time", "guild": guild.slug},
            **_HTMX,
        )
        assert response.status_code == 403

    def it_404s_on_an_unknown_guild_slug(db, client):
        _login(client, "miss_add_badslug")
        response = client.post(
            reverse("hub_wiki_wanted_request"),
            {"title": "epoxy cure time", "guild": "nope"},
            **_HTMX,
        )
        assert response.status_code == 404
