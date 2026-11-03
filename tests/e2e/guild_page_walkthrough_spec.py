"""End-to-end: the guild page's Walkthrough button starts the Guild Settings tour (#1084 follow-up).

The guild-lead tour's steps all live on Guild Settings, so Take on the guild page must land on that
same guild's Guild Settings with the first popover showing, not restart in place and find no step.
Checked for the guild's lead and for an admin who staffs no guild, with another guild sorting first
so an admin's fallback to their "first" guild would show. Waits are on what the page shows. Run with
``pytest -m e2e``, on PostgreSQL in CI.
"""

from __future__ import annotations

import re

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Guild, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _person(email: str, name: str, **fields: object) -> Member:
    MembershipPlanFactory()
    member = User.objects.create_user(username=email, email=email).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    for field, value in fields.items():
        setattr(member, field, value)
    member.save()
    return member


def _guild() -> Guild:
    GuildFactory(name="Aldergrove Guild", is_active=True)
    return GuildFactory(name="Thistlewood Guild", is_active=True)


def _take_on_the_guild_page(live_server, page, guild: Guild) -> None:
    page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}")
    page.locator('[data-walkthrough="guild-lead"] button[value="take"]').click()
    edit = reverse("hub_guild_edit", args=[guild.pk])
    expect(page).to_have_url(re.compile(rf"{re.escape(edit)}\?tour=guild-lead"))
    expect(page.locator(".driver-popover.pl-tour")).to_be_visible(timeout=10000)


def describe_the_guild_page_walkthrough():
    def it_starts_on_the_leads_guild_settings(live_server, page, login_via_code):
        email = "thistle-lead@example.com"
        guild = _guild()
        guild.guild_lead = _person(email, "Wren Thistlewood")
        guild.save(update_fields=["guild_lead"])
        login_via_code(email)
        _take_on_the_guild_page(live_server, page, guild)

    def it_starts_an_admin_on_the_guild_they_pressed_it_on(live_server, page, login_via_code):
        email = "thistle-admin@example.com"
        guild = _guild()
        _person(email, "Hester Galloway", fog_role=Member.FogRole.ADMIN)
        login_via_code(email)
        _take_on_the_guild_page(live_server, page, guild)
