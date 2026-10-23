"""The Holiday Bizarre sidebar entry: no feature switch, just who has a door (HQ card sharing).

An admin, by the effective role, sees it whenever any HQ exists; anyone else once a card on the open HQ is opened to
them (``hub.context_processors._hq_nav``). The sidebar is written three times in ``hub/base.html`` (the admin block,
the member block and the guest accounts' kiln menu), so each case reads the block it renders in. The entry costs one
query on every hub page, however many cards and people there are.
"""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import SiteConfiguration
from hq.models import HubCard
from hub.view_as import ROLE_MEMBER
from membership.models import Member
from tests.hq.conftest import signed_in
from tests.hq.factories import EventHubFactory, HubCardFactory

ENTRY = 'data-nav="hq"'
Kind = HubCard.Kind


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
    @pytest.fixture
    def hub(open_hub):
        return open_hub

    def it_shows_a_member_with_a_shared_card(hub, make_member):
        listed = make_member()
        HubCardFactory(hub=hub, kind=Kind.WINE_WALL, can_open=[listed])

        nav = _sidebar(signed_in(listed))

        assert _block(nav) == "member"
        assert ENTRY in nav

    def it_shows_a_member_on_a_cards_team(hub, make_member):
        team = make_member()
        HubCardFactory(hub=hub, kind=Kind.LEDGER, team=[team])

        assert ENTRY in _sidebar(signed_in(team))

    def it_hides_from_a_member_with_no_card(hub, member_client):
        HubCardFactory(hub=hub, kind=Kind.WINE_WALL)

        nav = _sidebar(member_client)

        assert _block(nav) == "member"
        assert ENTRY not in nav

    def it_shows_every_active_member_when_a_members_card_exists(hub, member_client):
        HubCardFactory(hub=hub, kind=Kind.VOLUNTEERS, audience=HubCard.Audience.MEMBERS)

        assert ENTRY in _sidebar(member_client)

    def it_ignores_a_hidden_card_a_closed_hub_and_a_team_only_door_opened_wider(make_member, member_client):
        closed = EventHubFactory(is_open=False)
        HubCardFactory(hub=closed, kind=Kind.VOLUNTEERS, audience=HubCard.Audience.MEMBERS)
        hub = EventHubFactory(is_open=True)
        HubCardFactory(hub=hub, kind=Kind.WINE_WALL, audience=HubCard.Audience.MEMBERS, is_hidden=True)
        HubCardFactory(hub=hub, kind=Kind.LEDGER, audience=HubCard.Audience.MEMBERS)

        assert ENTRY not in _sidebar(member_client)

    def it_follows_the_members_rule_for_an_admin_previewing_as_a_member(hub, admin, admin_client):
        _preview_as_member(admin_client)
        assert ENTRY not in _sidebar(admin_client)

        HubCardFactory(hub=hub, kind=Kind.MARKETING, team=[admin])
        nav = _sidebar(admin_client)

        assert _block(nav) == "member"
        assert ENTRY in nav


def describe_the_guest_block():
    @pytest.fixture(autouse=True)
    def kiln_open(db):
        config = SiteConfiguration.load()
        config.kiln_tickets_open = True
        config.save(update_fields=["kiln_tickets_open"])

    def it_shows_a_guest_account_a_card_opened_to_them(open_hub, make_member):
        guest = make_member(status=Member.Status.GUEST)
        HubCardFactory(hub=open_hub, kind=Kind.WINE_WALL, can_open=[guest])

        nav = _sidebar(signed_in(guest), reverse("kiln:mine"))

        assert _block(nav) == "guest"
        assert ENTRY in nav

    def it_hides_from_a_guest_account_with_none_and_a_members_card_is_not_theirs(open_hub, make_member):
        guest = make_member(status=Member.Status.GUEST)
        HubCardFactory(hub=open_hub, kind=Kind.VOLUNTEERS, audience=HubCard.Audience.MEMBERS)

        nav = _sidebar(signed_in(guest), reverse("kiln:mine"))

        assert _block(nav) == "guest"
        assert ENTRY not in nav


def describe_signed_out():
    def it_draws_no_sidebar_on_the_public_hq(open_hub):
        HubCardFactory(hub=open_hub, kind=Kind.WINE_WALL, audience=HubCard.Audience.PUBLIC)

        body = Client().get(reverse("hq:page", args=[open_hub.slug])).content.decode()

        assert 'class="hub-sidebar__nav"' not in body
        assert ENTRY not in body


def describe_the_query_cost():
    """One query on every hub page, whoever is looking and however many doors and people the HQ holds."""

    def _queries(client: Client) -> list[str]:
        with CaptureQueriesContext(connection) as captured:
            assert client.get(reverse("hub_home")).status_code == 200
        return [query["sql"] for query in captured.captured_queries]

    def _hq(queries: list[str]) -> list[str]:
        return [sql for sql in queries if '"hq_' in sql]

    def it_costs_a_member_one_query_that_stays_flat(open_hub, make_member):
        listed = make_member()
        HubCardFactory(hub=open_hub, kind=Kind.WINE_WALL, can_open=[listed])
        client = signed_in(listed)
        client.get(reverse("hub_home"))  # warm the per-session reads

        before = _queries(client)
        for kind in [Kind.MARKETING, Kind.SPONSORSHIP, Kind.LEDGER]:
            HubCardFactory(hub=open_hub, kind=kind, can_open=[make_member()], team=[make_member(), listed])
        after = _queries(client)

        assert len(_hq(before)) == 1
        assert len(after) == len(before)

    def it_costs_an_admin_one_query(open_hub, admin_client):
        admin_client.get(reverse("hub_home"))

        assert len(_hq(_queries(admin_client))) == 1
