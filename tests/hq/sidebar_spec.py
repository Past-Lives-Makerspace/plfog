"""The Holiday Bizarre sidebar entry: no feature switch, just who runs the HQ (#1307).

Whenever any HQ exists, admins and ACTIVE guild officers see it, by the effective role
(``hub.context_processors._hq_nav``, the rule of ``hq.access.can_run_hq``); nobody else does. The sidebar is written
three times in ``hub/base.html`` (the admin block, the member block and the guest accounts' kiln menu), so each case
reads the block it renders in. The entry costs at most two queries on every hub page, however many guilds and HQs.
"""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import SiteConfiguration
from hub.view_as import ROLE_MEMBER
from membership.models import Member
from tests.hq.conftest import signed_in
from tests.hq.factories import EventHubFactory
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

ENTRY = 'data-nav="hq"'


def _sidebar(client: Client, url: str | None = None) -> str:
    """The sidebar's <nav> on ``url`` (the hub home by default), and which block of base.html drew it."""
    body = client.get(url or reverse("hub_home")).content.decode()
    match = re.search(r'<nav class="hub-sidebar__nav".*?</nav>', body, re.S)
    assert match is not None, "sidebar nav not found"
    return match.group(0)


def _block(nav: str) -> str:
    """Which sidebar block ``nav`` is: the guest menu has Sign out and no home link; the admin block's Guild Voting
    goes to the admin overview, the member block's to the ballot."""
    if reverse("hub_home") not in nav:
        return "guest"
    return "admin" if reverse("hub_admin_voting_overview") in nav else "member"


def _preview_as_member(client: Client) -> None:
    session = client.session
    session["view_as_role"] = ROLE_MEMBER
    session.save()


def describe_the_admin_block():
    def it_shows_an_admin_the_entry_whenever_an_hq_exists_open_or_not(admin_client):
        assert ENTRY not in _sidebar(admin_client)

        EventHubFactory(is_open=False)
        nav = _sidebar(admin_client)

        assert _block(nav) == "admin"
        assert ENTRY in nav
        assert f'href="{reverse("hq:index")}"' in nav


def describe_the_member_block():
    def it_shows_a_guild_lead_the_entry(open_hub, officer_client):
        nav = _sidebar(officer_client)

        assert _block(nav) == "member"
        assert ENTRY in nav

    def it_shows_a_guild_staff_member_the_entry_for_a_closed_hq_too(make_officer):
        EventHubFactory(is_open=False)

        assert ENTRY in _sidebar(signed_in(make_officer(staff=True)))

    def it_hides_from_an_officer_while_no_hq_exists(officer_client):
        assert ENTRY not in _sidebar(officer_client)

    def it_hides_from_a_member_who_leads_or_staffs_no_guild(open_hub, member_client):
        nav = _sidebar(member_client)

        assert _block(nav) == "member"
        assert ENTRY not in nav

    def it_goes_on_the_next_request_once_they_stop_leading(open_hub, officer, officer_client):
        assert ENTRY in _sidebar(officer_client)

        officer.led_guilds.update(guild_lead=None)

        assert ENTRY not in _sidebar(officer_client)

    def it_follows_the_officer_rule_for_an_admin_previewing_as_a_member(open_hub, admin, admin_client):
        _preview_as_member(admin_client)
        assert ENTRY not in _sidebar(admin_client)

        GuildFactory(guild_lead=admin)
        nav = _sidebar(admin_client)

        assert _block(nav) == "member"
        assert ENTRY in nav


def describe_the_guest_block():
    @pytest.fixture(autouse=True)
    def kiln_open(db):
        config = SiteConfiguration.load()
        config.kiln_tickets_open = True
        config.save(update_fields=["kiln_tickets_open"])

    def it_never_shows_a_guest_account_the_entry_even_one_named_as_a_guild_lead(open_hub, make_officer):
        guest = make_officer(status=Member.Status.GUEST)

        nav = _sidebar(signed_in(guest), reverse("kiln:mine"))

        assert _block(nav) == "guest"
        assert ENTRY not in nav


def describe_the_query_cost():
    """At most two queries on every hub page (is this member an officer, does an HQ exist), however many there are."""

    def _queries(client: Client) -> list[str]:
        with CaptureQueriesContext(connection) as captured:
            assert client.get(reverse("hub_home")).status_code == 200
        return [query["sql"] for query in captured.captured_queries]

    def _hq(queries: list[str]) -> list[str]:
        return [sql for sql in queries if '"hq_' in sql]

    def it_costs_an_officer_the_same_queries_however_many_guilds_and_hqs(open_hub, officer, officer_client):
        officer_client.get(reverse("hub_home"))  # warm the per-session reads

        before = _queries(officer_client)
        for _ in range(3):
            GuildFactory(guild_lead=officer)
            GuildStaffMembershipFactory(member=officer)
            EventHubFactory(is_open=False)
        after = _queries(officer_client)

        assert len(_hq(before)) == 1
        assert len(after) == len(before)

    def it_costs_a_member_who_is_no_officer_no_hq_query(open_hub, member_client):
        member_client.get(reverse("hub_home"))

        assert _hq(_queries(member_client)) == []

    def it_costs_an_admin_one_query(open_hub, admin_client):
        admin_client.get(reverse("hub_home"))

        assert len(_hq(_queries(admin_client))) == 1
