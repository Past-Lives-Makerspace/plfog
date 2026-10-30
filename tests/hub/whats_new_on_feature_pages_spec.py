"""Every page a New! badge can lead to carries its What's New button (#1030).

A badge promises "something here is new"; the page it leads to must be able to show what. This
walks ``core.guidance.GUIDANCE`` (the registry, never a list kept by hand): each feature's page,
opened as a member every page admits (an admin who leads a guild, every switchable feature on),
must render ``{% whats_new_button %}`` for that key or a key covering it (``guild`` covers
``guild.*``), and the ``whats-new`` modal must come from ``hub/base.html`` exactly once. A feature
registered later without a button fails here, naming the page.
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
from membership.models import Member
from tests.features import turn_on
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

KEY_ATTR = re.compile(r'data-whats-new-key="([^"]*)"')


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
