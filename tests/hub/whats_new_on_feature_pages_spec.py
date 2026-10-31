"""Every page a New! badge can lead to carries its What's New button (#1030), and the modal never links to the page it is on (#1051).

A badge promises "something here is new"; the page it leads to must be able to show what. This
walks ``core.guidance.GUIDANCE`` (the registry, never a list kept by hand): each feature's page,
opened as a member every page admits (an admin who leads a guild, every switchable feature on),
must render ``{% whats_new_button %}`` for that key or a key covering it (``guild`` covers
``guild.*``), and the ``whats-new`` modal must come from ``hub/base.html`` exactly once. A feature
registered later without a button fails here, naming the page. The second walk opens the modal on
each of those pages (htmx's ``HX-Current-URL``) and fails on any Open <feature> whose path is the
page itself: there it is Show me, with a target, or no button.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.guidance import GUIDANCE, covers, url_for
from core.whats_new import TABS
from membership.models import Member
from tests.features import turn_on
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

KEY_ATTR = re.compile(r'data-whats-new-key="([^"]*)"')
OPEN_HREF = re.compile(r'href="([^"]*)" class="hub-btn hub-btn--sm" data-whats-new-open')


def _entries() -> list[dict[str, Any]]:
    """One entry per registered key, dated today, for everyone: every button renders and pulses."""
    today = timezone.localdate().isoformat()
    return [
        {
            "date": today,
            "title": key,
            "changes": [f"{key} changed"],
            "slug": key.replace(".", "-"),
            "audiences": ["members", "guild-staff", "admins"],
            "feature": key,
            "guidance": "none",
        }
        for key in GUIDANCE
    ]


@pytest.fixture
def everything_new(monkeypatch):
    monkeypatch.setattr("plfog.version.CHANGELOG", _entries())


@pytest.fixture
def admin_lead(client: Client) -> Member:
    """An admin who leads a guild with every tab on, signed in, with every switchable feature on."""
    MembershipPlanFactory()
    for key in ("catalog", "teach", "meetings", "orientations", "equipment", "voting", "wiki", "guilds"):
        turn_on(key)
    user = User.objects.create_user(username="lead", password="pass")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    GuildFactory(guild_lead=member, show_cubbies_tab=True)
    client.login(username="lead", password="pass")
    return member


def describe_every_feature_page():
    @pytest.mark.parametrize("key", sorted(GUIDANCE))
    def it_carries_the_whats_new_button_for_its_key(client: Client, everything_new, admin_lead, key):
        url = url_for(key, admin_lead)
        response = client.get(url, follow=True)
        assert response.status_code == 200, f"{key}: {url} answered {response.status_code}"
        html = response.content.decode()
        found = KEY_ATTR.findall(html)
        if url.split("?")[0] == reverse("hub_home"):
            # Home's labelled button is over every entry: no key on it.
            assert "data-whats-new-button" in html, f"{key}: Home shows no What's New button"
        else:
            assert any(covers(shown, key) for shown in found), (
                f"{key}: {url} shows no What's New button for it; buttons on the page: {found or 'none'}"
            )
        assert html.count('id="whats-new-body"') == 1, f"{key}: the whats-new modal must come from hub/base.html once"

    def it_walks_every_registered_key():
        # The registry is the list: a key added to core/guidance.py is walked without an edit here.
        assert len(GUIDANCE) >= 30


def _modal_on(client: Client, page_url: str, tab: str) -> str:
    """The modal body as htmx loads it with the modal open on ``page_url``."""
    response = client.get(
        reverse("hub_whats_new") + f"?tab={tab}",
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL=f"http://testserver{page_url}",
    )
    assert response.status_code == 200
    return response.content.decode()


def describe_the_modal_open_on_every_feature_page():
    @pytest.mark.parametrize("key", sorted(GUIDANCE))
    def it_never_links_to_the_page_it_is_open_on(client: Client, everything_new, admin_lead, key):
        url = url_for(key, admin_lead)
        path = url.split("?")[0]
        for tab in TABS:
            html = _modal_on(client, url, tab)
            own = [href for href in OPEN_HREF.findall(html) if href.split("?")[0] == path]
            assert not own, f"{key}: the modal open on {url} links to it: {own}"
            # No entry names a target here, so the page's own entry draws no button at all.
            assert "data-whats-new-show" not in html, f"{key}: Show me with no target on the {tab} tab"

    def it_shows_me_for_an_entry_that_names_a_control_on_that_page(client: Client, monkeypatch, admin_lead):
        entries = _entries()
        teach = next(entry for entry in entries if entry["feature"] == "classes.teach")
        teach["target"] = "classes.new-class"
        monkeypatch.setattr("plfog.version.CHANGELOG", entries)
        html = _modal_on(client, url_for("classes.teach", admin_lead), "members")
        assert 'data-whats-new-show="classes.new-class"' in html and ">Show me</button>" in html
        assert not [href for href in OPEN_HREF.findall(html) if href.split("?")[0] == reverse("classes:teach_overview")]
