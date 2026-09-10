"""Specs for the Overdue For Review panel and the one-tap confirm it drives."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import Member, WikiPage
from tests.membership.factories import GuildFactory, MembershipPlanFactory, WikiPageFactory

pytestmark = pytest.mark.django_db

_HTMX = {"HTTP_HX_REQUEST": "true"}


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


def _aged(page: WikiPage, *, days: int) -> WikiPage:
    WikiPage.objects.filter(pk=page.pk).update(created_at=timezone.now() - timedelta(days=days))
    page.refresh_from_db()
    return page


def _panel(client: Client, guild) -> str:
    """The Overdue For Review card's markup, or "" when the card does not render.

    Bounded by the confirm-cue anchor that closes the card and NOT by the next panel's
    heading: every panel in that column is gated on having rows of its own now, so the one
    that used to follow this card may simply not be there.
    """
    html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
    if "Overdue For Review" not in html:
        return ""
    return html.split("Overdue For Review", 1)[1].split('id="wiki-confirm-cue"', 1)[0]


def _overdue_machine(guild, title: str = "Old Saw") -> WikiPage:
    """A page that IS overdue, so the panel renders and an absence test can discriminate.

    Without one of these the panel is not on the page at all, and "the page I am looking
    for is not in the panel" passes against an empty string no matter what the code does.
    """
    return _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title=title), days=400)


def describe_what_is_in_the_list():
    def it_lists_a_machine_page_past_its_twelve_months(db, client):
        user = _login(client, "overdue_machine")
        guild = GuildFactory(guild_lead=user.member)
        _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Old Saw"), days=400)
        assert "Old Saw" in _panel(client, guild)

    def it_leaves_a_howto_of_the_same_age_alone(db, client):
        """How-tos get 24 months, machines 12. Kind drives the clock."""
        user = _login(client, "overdue_howto")
        guild = GuildFactory(guild_lead=user.member)
        _overdue_machine(guild)
        _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.HOWTO, title="Young Howto"), days=400)
        panel = _panel(client, guild)
        assert "Old Saw" in panel
        assert "Young Howto" not in panel

    def it_does_not_render_the_panel_at_all_when_nothing_is_overdue(db, client):
        # A card whose entire content is "Nothing overdue. Everything here has been checked
        # recently." explains a review clock to somebody who has nothing to review. On a
        # wiki that launched empty that sentence was most of the guild tab.
        user = _login(client, "overdue_none")
        guild = GuildFactory(guild_lead=user.member)
        _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.HOWTO), days=10)
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "Overdue For Review" not in html
        assert "Nothing overdue." not in html

    def it_never_lists_a_project_page(db, client):
        """A project write-up is a record of what somebody did once; re-confirming it every
        year would be asking them to do it again."""
        user = _login(client, "overdue_project")
        guild = GuildFactory(guild_lead=user.member)
        _overdue_machine(guild)
        _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.PROJECT, title="Ancient Project"), days=5000)
        panel = _panel(client, guild)
        assert "Old Saw" in panel
        assert "Ancient Project" not in panel

    def it_never_lists_a_reported_page_however_stale(db, client):
        user = _login(client, "overdue_reported")
        guild = GuildFactory(guild_lead=user.member)
        _overdue_machine(guild)
        page = _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Reported Saw"), days=5000)
        WikiPage.objects.filter(pk=page.pk).update(needs_review_since=timezone.now())
        panel = _panel(client, guild)
        assert "Old Saw" in panel
        assert "Reported Saw" not in panel

    def it_says_never_checked_when_nobody_has(db, client):
        user = _login(client, "overdue_never")
        guild = GuildFactory(guild_lead=user.member)
        _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE), days=400)
        assert "never checked" in _panel(client, guild)

    def it_offers_an_edit_link_so_the_panel_is_not_a_dead_end(db, client):
        user = _login(client, "overdue_edit")
        guild = GuildFactory(guild_lead=user.member)
        page = _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE), days=400)
        assert reverse("hub_wiki_edit", args=[page.slug]) in _panel(client, guild)

    def it_links_to_the_scoped_stale_search_past_eight_rows(db, client):
        user = _login(client, "overdue_seeall")
        guild = GuildFactory(guild_lead=user.member)
        for index in range(9):
            _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title=f"Saw {index}"), days=400)
        panel = _panel(client, guild)
        assert "See all 9" in panel
        assert f"{reverse('hub_wiki_search')}?guild={guild.slug}&amp;stale=1" in panel


def describe_still_accurate():
    def it_resets_the_clock_without_an_editor_or_a_revision(db, client):
        """Confirming has to stay cheaper than editing or nothing gets confirmed."""
        user = _login(client, "overdue_confirm")
        guild = GuildFactory(guild_lead=user.member)
        page = _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE), days=400)
        before = page.revisions.count()
        response = client.post(reverse("hub_wiki_confirm", args=[page.slug]), **_HTMX)
        assert response.status_code == 200
        assert response.content.decode().strip() != ""
        page.refresh_from_db()
        assert page.last_checked_at is not None
        assert page.last_checked_by_id == user.member.pk
        assert page.revisions.count() == before
        assert page.is_out_of_date is False

    def it_removes_the_row_from_the_panel_on_the_next_render(db, client):
        user = _login(client, "overdue_gone")
        guild = GuildFactory(guild_lead=user.member)
        page = _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE, title="Old Saw"), days=400)
        assert "Old Saw" in _panel(client, guild)
        client.post(reverse("hub_wiki_confirm", args=[page.slug]), **_HTMX)
        assert "Old Saw" not in _panel(client, guild)

    def it_is_open_to_any_active_member(db, client):
        """Spec A's endpoint is deliberately un-gated: one-tap confirmation is the brief's
        answer to stale content, and restricting it to leads would kill it. The PANEL is
        lead-only because a work queue nobody can work is noise — a different question."""
        _login(client, "overdue_plain_member")
        guild = GuildFactory()
        page = _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE), days=400)
        response = client.post(reverse("hub_wiki_confirm", args=[page.slug]), **_HTMX)
        assert response.status_code == 200
        assert _panel(client, guild) == ""

    def it_carries_a_toast(db, client):
        user = _login(client, "overdue_toast")
        guild = GuildFactory(guild_lead=user.member)
        page = _aged(WikiPageFactory(guild=guild, kind=WikiPage.Kind.MACHINE), days=400)
        response = client.post(reverse("hub_wiki_confirm", args=[page.slug]), **_HTMX)
        assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "success"


def describe_the_panels_help_bubble():
    def it_spells_out_every_interval(db, client):
        user = _login(client, "overdue_help")
        guild = GuildFactory(guild_lead=user.member)
        _overdue_machine(guild)
        panel = _panel(client, guild)
        assert "pl-help__bubble" in panel
        assert "Machines every 12 months" in panel
        assert "Project pages never expire." in panel
