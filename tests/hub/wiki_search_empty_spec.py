"""Specs for the zero-result search screen and the miss it logs."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member, WikiSearchMiss, WikiWantedPage
from tests.membership.factories import (
    GuildFactory,
    MembershipPlanFactory,
    WikiPageFactory,
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


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = Member.FogRole.MEMBER
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _search(client: Client, query: str, guild=None) -> str:
    url = f"{reverse('hub_wiki_search')}?q={query}"
    if guild is not None:
        url = f"{url}&guild={guild.slug}"
    return client.get(url).content.decode()


def describe_the_screen():
    def it_quotes_what_they_typed(db, client):
        _login(client, "empty_headline")
        assert "Nothing found for &ldquo;epoxy cure time&rdquo;." in _search(client, "epoxy cure time")

    def it_offers_search_everything_only_on_a_scoped_search(db, client):
        _login(client, "empty_scope")
        guild = GuildFactory(name="Woodworking")
        scoped = _search(client, "epoxy", guild)
        assert "Search Everything" in scoped
        assert "You searched <strong>Woodworking</strong> only." in scoped
        assert "Search Everything" not in _search(client, "epoxy")

    def it_offers_to_write_it_yourself(db, client):
        _login(client, "empty_write")
        html = _search(client, "epoxy cure time")
        assert "write it yourself" in html
        assert f"{reverse('hub_wiki_new')}?title=epoxy%20cure%20time" in html


def describe_the_discord_button():
    def it_deep_links_the_guilds_channel_when_both_ids_are_set(db, client, _wiki_on):
        _wiki_on.discord_server_id = "111"
        _wiki_on.save()
        _login(client, "empty_discord_channel")
        guild = GuildFactory(discord_channel_id="222", discord_channel_name="#woodworking")
        html = _search(client, "epoxy", guild)
        assert "https://discord.com/channels/111/222" in html
        assert "Ask In #woodworking" in html

    def it_falls_back_to_the_server_with_only_a_server_id(db, client, _wiki_on):
        _wiki_on.discord_server_id = "111"
        _wiki_on.save()
        _login(client, "empty_discord_server")
        guild = GuildFactory(discord_channel_id="")
        html = _search(client, "epoxy", guild)
        assert 'https://discord.com/channels/111"' in html
        assert "Ask On Discord" in html

    def it_is_absent_with_neither_configured(db, client, _wiki_on):
        """A broken Discord link is worse than no button."""
        _wiki_on.discord_server_id = ""
        _wiki_on.save()
        _login(client, "empty_discord_none")
        html = _search(client, "epoxy", GuildFactory(discord_channel_id=""))
        card = html.split("Nothing found for", 1)[1].split("</div>", 3)[0]
        assert "discord.com/channels" not in card
        assert "Ask On Discord" not in html


def describe_request_this_page():
    def it_posts_the_exact_query_as_the_title(db, client):
        _login(client, "empty_request")
        guild = GuildFactory()
        response = client.post(
            reverse("hub_wiki_wanted_request"),
            {"title": "epoxy cure time", "guild": guild.slug, "surface": "empty"},
            **_HTMX,
        )
        assert response.status_code == 200
        row = WikiWantedPage.objects.get()
        assert (row.title, row.guild_id, row.request_count) == ("epoxy cure time", guild.pk, 1)
        toast = json.loads(response["HX-Trigger"])["showToast"]
        assert toast["message"] == "Added to Wanted pages. Your guild's leads will see it."

    def it_says_which_number_asker_they_are_on_a_bump(db, client):
        """Telling somebody they are the fourth beats a silent duplicate."""
        _login(client, "empty_request_bump")
        guild = GuildFactory()
        WikiWantedPageFactory(guild=guild, title="Epoxy cure time")
        response = client.post(
            reverse("hub_wiki_wanted_request"),
            {"title": "epoxy cure time", "guild": guild.slug, "surface": "empty"},
            **_HTMX,
        )
        toast = json.loads(response["HX-Trigger"])["showToast"]
        assert toast["message"] == "Already on the list — you're the 2nd person to ask."

    def it_replaces_the_button_so_it_cannot_be_tapped_twice(db, client):
        _login(client, "empty_request_swap")
        response = client.post(
            reverse("hub_wiki_wanted_request"),
            {"title": "epoxy cure time", "surface": "empty"},
            **_HTMX,
        )
        body = response.content.decode()
        assert "On the wanted list" in body
        assert "Request This Page" not in body


def describe_the_failed_search_log():
    def it_records_a_zero_result_search(db, client):
        user = _login(client, "empty_logs")
        guild = GuildFactory()
        _search(client, "epoxy cure time", guild)
        miss = WikiSearchMiss.objects.get()
        assert (miss.query, miss.guild_id, miss.member_id) == ("epoxy cure time", guild.pk, user.member.pk)

    def it_records_nothing_when_the_wiki_answered(db, client):
        _login(client, "empty_no_log_hit")
        guild = GuildFactory()
        WikiPageFactory(guild=guild, title="Epoxy cure time")
        _search(client, "epoxy", guild)
        assert WikiSearchMiss.objects.count() == 0

    def it_records_nothing_on_a_browse_with_no_query(db, client):
        _login(client, "empty_no_log_browse")
        client.get(reverse("hub_wiki_search"))
        assert WikiSearchMiss.objects.count() == 0

    def it_records_nothing_when_the_help_centre_answered(db, client, _wiki_on):
        """The panel is titled "Searches That Found Nothing" and has to mean it."""
        from tests.membership.factories import WikiArticleFactory

        _wiki_on.help_page_enabled = True
        _wiki_on.save()
        _login(client, "empty_no_log_help")
        WikiArticleFactory(title="Epoxy cure time", body="How the app handles epoxy.")
        _search(client, "epoxy cure time")
        assert WikiSearchMiss.objects.count() == 0
