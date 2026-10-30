"""BDD specs for where the New! badge and the What's New button land (#1007, part 3): the guild
page's section tabs and statbar, Admin Tools and Guild Tools, Home's quick links and the sidebar.

The changelog is patched per spec (``plfog.version.CHANGELOG``, read at call time), so nothing here
depends on the repo's fragments or on the calendar. Assertions anchor on markup (STANDARDS.md §8).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.features import turn_on
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _entry(slug: str, *, feature: str, audiences: tuple[str, ...] = ("members",), days_ago: int = 0) -> dict[str, Any]:
    return {
        "date": (timezone.localdate() - dt.timedelta(days=days_ago)).isoformat(),
        "title": slug.replace("-", " ").capitalize(),
        "changes": [f"{slug} changed"],
        "slug": slug,
        "audiences": list(audiences),
        "feature": feature,
        "guidance": "link",
    }


@pytest.fixture
def changelog(monkeypatch):
    def _set(entries: list[dict[str, Any]]) -> None:
        monkeypatch.setattr("plfog.version.CHANGELOG", entries)

    return _set


def _login(client: Client, username: str = "viewer", **fields: Any) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    for name, value in fields.items():
        setattr(member, name, value)
    if fields:
        member.save(update_fields=list(fields))
        member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _badge(key: str) -> str:
    return f'<span class="pl-new" data-new-key="{key}">New!</span>'


def describe_the_guild_page():
    def it_badges_the_cubbies_tab_and_puts_the_guild_button_before_the_cta(client: Client, changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        _login(client)
        guild = GuildFactory(show_cubbies_tab=True)
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert f'data-guild-tab="cubbies">Cubbies{_badge("guild.cubbies")}</button>' in html
        assert html.index('class="pl-guild-statbar__actions"') < html.index('data-whats-new-key="guild"')
        assert html.index('data-whats-new-key="guild"') < html.index('id="guild-join-cta"')
        assert 'hx-get="/whats-new/?feature=guild"' in html and 'id="whats-new-body"' in html
        # The button row's picker gets its badge and dot from the mirror; the server paints the dot for the first tab.
        assert 'x-show="newElsewhere()" data-tab-picker-dot' in html and "x-cloak data-tab-picker-dot" not in html

    def it_shows_neither_once_the_entry_is_seen_and_opened_and_no_entry_names_the_guild(client: Client, changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        _login(client, whats_new_seen=["cubbies"], whats_new_opened=["cubbies"])
        guild = GuildFactory(show_cubbies_tab=True)
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "data-new-key" not in html
        assert 'data-whats-new-key="guild"' in html and "pl-whats-new-btn--unseen" not in html
        changelog([_entry("events", feature="calendar.events")])
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "data-whats-new-button" not in html


def describe_the_tools_page():
    def it_gives_an_admin_the_admin_button_and_badges_a_card(client: Client, changelog):
        changelog([_entry("recon", feature="admin.reconciliation", audiences=("admins",))])
        _login(client, "admin", fog_role=Member.FogRole.ADMIN)
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert html.index("data-tools-page-title>Admin Tools") < html.index('data-whats-new-key="admin"')
        assert 'hx-get="/whats-new/?feature=admin"' in html and 'data-whats-new-key="guild"' not in html
        assert f'<span class="pl-tool-card__title">Reports{_badge("admin.reconciliation")}</span>' in html
        assert html.count("data-new-key=") == 1

    def it_gives_a_guild_lead_the_guild_button_and_never_the_admin_one(client: Client, changelog):
        changelog(
            [
                _entry("cubbies", feature="guild.cubbies", audiences=("guild-staff",)),
                _entry("recon", feature="admin.reconciliation", audiences=("admins",)),
            ]
        )
        lead = _login(client, "lead")
        GuildFactory(guild_lead=lead, show_cubbies_tab=True)
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert "data-tools-page-title>Guild Tools" in html
        assert 'data-whats-new-key="guild"' in html and 'data-whats-new-key="admin"' not in html
        assert f'<span class="pl-tool-card__title">Cubbies{_badge("guild.cubbies")}</span>' in html
        assert "admin.reconciliation" not in html


def describe_home_and_the_sidebar():
    def it_badges_a_quick_link_and_the_matching_sidebar_links(client: Client, changelog):
        changelog([_entry("events", feature="calendar.events"), _entry("voting", feature="guild.voting")])
        _login(client)
        turn_on("voting")
        html = client.get(reverse("hub_home")).content.decode()
        assert f'<span class="pl-quicklink__label">Calendar{_badge("calendar.events")}</span>' in html
        assert f'<span class="pl-quicklink__label">Guild Voting{_badge("guild.voting")}</span>' in html
        assert f"Calendar{_badge('calendar.events')}\n            </a>" in html
        assert f"Guild Voting{_badge('guild.voting')}\n</a>" in html
        assert html.count(_badge("calendar.events")) == 2 and html.count(_badge("guild.voting")) == 2
