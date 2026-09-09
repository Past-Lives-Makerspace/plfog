"""Specs for POST /wiki/p/<slug>/verify/ and the control it swaps back."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member, WikiPage
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    WikiPageFactory,
)

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


def _toast(response) -> dict:
    return json.loads(response["HX-Trigger"])["showToast"]


def describe_the_one_tap_verify():
    def it_answers_200_with_a_body_carrying_the_oob_swap(db, client, stub_page_verified_event):
        """Not 204: a 204 has no body, so it could not carry the swap and the toast would
        fire while a stale Community pill sat there until the next reload."""
        user = _login(client, "verify_view_lead")
        guild = GuildFactory(name="Woodworking", guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        response = client.post(reverse("hub_wiki_verify", args=[page.slug]), **_HTMX)
        assert response.status_code == 200
        body = response.content.decode()
        assert body.strip() != ""
        assert 'hx-swap-oob="true"' in body
        assert "Guild verified" in body
        # The swapped-in control carries a live CSRF token, so Re-verify still works
        # without a reload.
        assert "csrfmiddlewaretoken" in body
        assert _toast(response)["message"] == "Verified. Thanks for reading it."
        page.refresh_from_db()
        assert page.status == WikiPage.Status.GUILD_VERIFIED

    def it_redirects_with_a_message_for_a_plain_post(db, client, stub_page_verified_event):
        user = _login(client, "verify_view_plainpost")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        response = client.post(reverse("hub_wiki_verify", args=[page.slug]))
        assert response.status_code == 302
        assert response["Location"] == page.get_absolute_url()

    def it_treats_a_boosted_post_as_a_full_page_post(db, client, stub_page_verified_event):
        """hub/base.html boosts the whole body, so a plain form arrives carrying
        HX-Request too. Answering that with a fragment swaps a bare div in for the page."""
        user = _login(client, "verify_view_boosted")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        response = client.post(
            reverse("hub_wiki_verify", args=[page.slug]),
            HTTP_HX_REQUEST="true",
            HTTP_HX_BOOSTED="true",
        )
        assert response.status_code == 302

    def it_stores_the_note_from_the_modal(db, client, stub_page_verified_event):
        user = _login(client, "verify_view_note")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        client.post(reverse("hub_wiki_verify", args=[page.slug]), {"note": "Blade guard checked."}, **_HTMX)
        page.refresh_from_db()
        assert page.verified_note == "Blade guard checked."

    def it_refuses_a_note_over_the_column_width(db, client):
        user = _login(client, "verify_view_longnote")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        response = client.post(reverse("hub_wiki_verify", args=[page.slug]), {"note": "x" * 400}, **_HTMX)
        assert response.status_code == 400
        assert _toast(response)["type"] == "error"
        page.refresh_from_db()
        assert page.status == WikiPage.Status.COMMUNITY


def describe_removing_a_verification():
    def it_drops_the_page_back_to_community(db, client, stub_page_verified_event):
        user = _login(client, "verify_view_remove")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        page.verify(user.member)
        response = client.post(reverse("hub_wiki_verify", args=[page.slug]), {"remove": "1"}, **_HTMX)
        assert response.status_code == 200
        assert _toast(response)["message"] == "Verification removed."
        page.refresh_from_db()
        assert page.status == WikiPage.Status.COMMUNITY


def describe_the_permission_gate():
    def it_refuses_a_plain_member(db, client):
        _login(client, "verify_view_plain")
        page = WikiPageFactory(guild=GuildFactory())
        response = client.post(reverse("hub_wiki_verify", args=[page.slug]), **_HTMX)
        assert response.status_code == 403

    def it_refuses_an_official_page_even_to_an_admin(db, client):
        _login(client, "verify_view_official", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=GuildFactory(), official=True)
        assert client.post(reverse("hub_wiki_verify", args=[page.slug]), **_HTMX).status_code == 403

    def it_answers_400_on_an_archived_page(db, client):
        user = _login(client, "verify_view_archived")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild, archived=True)
        response = client.post(reverse("hub_wiki_verify", args=[page.slug]), **_HTMX)
        assert response.status_code == 400
        assert _toast(response)["message"] == "This page is archived."

    def it_refuses_a_member_whose_membership_is_not_active(db, client):
        user = _login(client, "verify_view_lapsed")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        user.member.status = Member.Status.FORMER
        user.member.save(update_fields=["status"])
        assert client.post(reverse("hub_wiki_verify", args=[page.slug]), **_HTMX).status_code == 403

    def it_404s_on_a_page_the_safety_gate_is_holding_back(db, client):
        """Spec D's gate has to be a gate on every path, not only on the lists."""
        _login(client, "verify_view_held")
        guild = GuildFactory()
        page = WikiPageFactory(guild=guild, is_published=False)
        assert client.post(reverse("hub_wiki_verify", args=[page.slug]), **_HTMX).status_code == 404

    def it_reports_a_domain_error_through_messages_on_a_plain_post(db, client):
        user = _login(client, "verify_view_plain_error")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild, archived=True)
        response = client.post(reverse("hub_wiki_verify", args=[page.slug]), follow=True)
        assert response.status_code == 200
        assert "This page is archived." in [m.message for m in response.context["messages"]]

    def it_404s_for_an_unknown_slug(db, client):
        _login(client, "verify_view_404")
        assert client.post(reverse("hub_wiki_verify", args=["nope"]), **_HTMX).status_code == 404

    def it_404s_while_the_wiki_is_off(db, client, _wiki_on):
        _wiki_on.wiki_enabled = False
        _wiki_on.save()
        user = _login(client, "verify_view_flagoff")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        assert client.post(reverse("hub_wiki_verify", args=[page.slug]), **_HTMX).status_code == 404


def describe_the_control_on_the_reading_page():
    def _page_html(client: Client, page: WikiPage) -> str:
        return client.get(page.get_absolute_url()).content.decode()

    def it_offers_verify_to_a_verifier(db, client):
        user = _login(client, "verify_ctl_lead")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        html = _page_html(client, page)
        assert ">Verify</button>" in html
        assert "with a note" in html

    def it_offers_nothing_to_a_plain_member(db, client):
        _login(client, "verify_ctl_plain")
        page = WikiPageFactory(guild=GuildFactory())
        html = _page_html(client, page)
        assert ">Verify</button>" not in html
        assert "Remove verification" not in html

    def it_shows_the_credit_line_to_everyone(db, client, stub_page_verified_event):
        """The brief wants a member to read "Verified by Kate (Woodworking orienter)"."""
        orienter = _member_user("verify_ctl_orienter")
        guild = GuildFactory(name="Woodworking")
        GuildStaffMembershipFactory(guild=guild, member=orienter.member, role="orienter")
        page = WikiPageFactory(guild=guild)
        page.verify(orienter.member, note="Blade guard checked.")
        _login(client, "verify_ctl_reader")
        html = _page_html(client, page)
        assert "Woodworking orienter" in html
        assert "Blade guard checked." in html
        assert ">Verify</button>" not in html

    def it_offers_remove_only_once_the_page_is_verified(db, client, stub_page_verified_event):
        user = _login(client, "verify_ctl_remove")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        assert "Remove verification" not in _page_html(client, page)
        page.verify(user.member)
        html = _page_html(client, page)
        assert "Remove verification" in html
        assert ">Re-verify</button>" in html

    def it_renders_nothing_on_an_official_page(db, client):
        _login(client, "verify_ctl_official", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=GuildFactory(), official=True)
        html = _page_html(client, page)
        assert ">Verify</button>" not in html


def describe_the_compact_control_in_a_tab_row():
    def _tab_html(client: Client, guild) -> str:
        return client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()

    def it_renders_the_button_for_a_verifier_on_a_community_page(db, client):
        user = _login(client, "verify_tab_lead")
        guild = GuildFactory(guild_lead=user.member)
        WikiPageFactory(guild=guild)
        html = _tab_html(client, guild)
        assert 'name="surface" value="tab"' in html
        assert ">Verify</button>" in html

    def it_renders_no_button_for_a_plain_member(db, client):
        _login(client, "verify_tab_plain")
        guild = GuildFactory()
        WikiPageFactory(guild=guild)
        assert 'name="surface" value="tab"' not in _tab_html(client, guild)

    def it_renders_no_button_on_an_already_verified_page(db, client):
        user = _login(client, "verify_tab_done")
        guild = GuildFactory(guild_lead=user.member)
        WikiPageFactory(guild=guild, verified=True)
        assert 'name="surface" value="tab"' not in _tab_html(client, guild)

    def it_renders_no_button_on_an_official_page(db, client):
        user = _login(client, "verify_tab_official")
        guild = GuildFactory(guild_lead=user.member)
        WikiPageFactory(guild=guild, official=True)
        assert 'name="surface" value="tab"' not in _tab_html(client, guild)

    def it_swaps_the_whole_row_back(db, client, stub_page_verified_event):
        user = _login(client, "verify_tab_swap")
        guild = GuildFactory(guild_lead=user.member)
        page = WikiPageFactory(guild=guild)
        response = client.post(reverse("hub_wiki_verify", args=[page.slug]), {"surface": "tab"}, **_HTMX)
        body = response.content.decode()
        assert response.status_code == 200
        assert f'id="wiki-tab-row-{page.pk}"' in body
        assert 'hx-swap-oob="true"' in body
        assert "Guild verified" in body
        assert 'value="tab"' not in body  # the button is gone, the chip is there
