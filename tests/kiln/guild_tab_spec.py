"""Kiln Tickets is a tab on the Ceramics Guild page (#691), and ``/kiln/`` sends everyone but a guest there.

The tab shows on the kiln guild's page alone, on the members surface, to a signed in maker:
everyone while the launch switch is on, the crew and site admins while it is off
(``launch_switch_spec`` covers the off half). A guest keeps ``/kiln/``: the lockout never lets
them onto a guild page.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from kiln.access import kiln_home_url
from kiln.models import KilnFiring, KilnTicket
from kiln.services import HISTORY_PAGE, guild_kiln_home
from membership.models import Member
from tests.kiln.conftest import signed_in
from tests.kiln.factories import KilnTicketFactory, KilnTicketPhotoFactory
from tests.membership.factories import GuildFactory, GuildMembershipFactory

pytestmark = pytest.mark.django_db

MINE = reverse("kiln:mine")
TAB = 'data-guild-tab="kiln"'
GUILDS_HOST = "guilds.pastlives.space"


def _page(client: Client, slug: str = "ceramics-guild", query: str = "?tab=kiln", **extra: str) -> str:
    response = client.get(f"{reverse('hub_guild_detail', args=[slug])}{query}", **extra)
    assert response.status_code == 200
    return response.content.decode()


def _pane(body: str) -> str:
    return body.split("data-guild-kiln>")[1].split("</section>\n</div>")[0]


def _firing(by: Member) -> KilnFiring:
    """A firing still in the kiln."""
    return KilnFiring.start(KilnTicket.FiringType.BISQUE, by=by)


def describe_the_tab():
    def it_shows_for_a_guild_member_while_open(kiln_guild, maker, maker_client):
        GuildMembershipFactory(guild=kiln_guild, member=maker)

        body = _page(maker_client)

        assert TAB in body
        assert "if (t === 'kiln') section = 'kiln';" in body
        assert "css/kiln-tickets.css" in body

    def it_shows_for_any_signed_in_member_while_open(kiln_guild, maker_client):
        assert TAB in _page(maker_client)

    def it_never_shows_on_another_guilds_page(kiln_guild, crew_client):
        other = GuildFactory(name="Print Guild", slug="print-guild")

        body = _page(crew_client, other.slug)

        assert TAB not in body and "data-guild-kiln" not in body
        assert "css/kiln-tickets.css" not in body

    def it_never_shows_to_a_visitor_who_is_not_signed_in(kiln_guild):
        assert TAB not in _page(Client())

    def it_never_shows_on_the_guilds_surface(kiln_guild, crew):
        settings = dict(
            ALLOWED_HOSTS=[GUILDS_HOST, "testserver"],
            GUILDS_HOSTS=[GUILDS_HOST],
            GUILDS_BASE_URL=f"https://{GUILDS_HOST}",
        )
        with override_settings(**settings):
            response = signed_in(crew).get(f"/guilds/{kiln_guild.slug}/", HTTP_HOST=GUILDS_HOST)

        assert response.status_code == 200
        assert TAB not in response.content.decode()

    def it_follows_view_as_for_an_admin_while_the_switch_is_off(kiln_guild, make_member):
        from tests.kiln.conftest import set_kiln_open

        set_kiln_open(False)
        admin = make_member(fog_role=Member.FogRole.ADMIN)

        as_admin = guild_kiln_home(kiln_guild, admin, guilds_surface=False, show_older=False, acting_admin=True)
        as_member = guild_kiln_home(kiln_guild, admin, guilds_surface=False, show_older=False, acting_admin=False)

        assert as_admin is not None
        assert as_member is None

    def it_skips_a_former_member_and_nobody(kiln_guild, make_member):
        assert guild_kiln_home(kiln_guild, None, guilds_surface=False, show_older=False, acting_admin=False) is None
        former = make_member(status=Member.Status.FORMER)
        assert guild_kiln_home(kiln_guild, former, guilds_surface=False, show_older=False, acting_admin=False) is None


def describe_the_tab_pane():
    def it_lists_the_makers_tickets_by_status_like_my_tickets(kiln_guild, maker, maker_client):
        draft = KilnTicketFactory(maker=maker)
        queued = KilnTicketFactory(maker=maker, status="submitted", submitted_at=timezone.now())
        fired = KilnTicketFactory(maker=maker, status="fired", fired_at=timezone.now())
        KilnTicketFactory()  # someone else's

        pane = _pane(_page(maker_client))

        assert f'href="{reverse("kiln:new")}"' in pane
        assert f'data-ticket="{draft.pk}"' in pane and f'data-ticket="{queued.pk}"' in pane
        assert f'data-ticket="{fired.pk}"' in pane
        assert pane.count("data-ticket=") == 3
        assert 'data-group="submitted"' in pane and 'data-group="history"' in pane

    def it_says_so_when_there_are_no_tickets(kiln_guild, maker_client):
        assert "No tickets yet." in _pane(_page(maker_client))

    def it_pages_history_on_the_guild_tab(kiln_guild, maker, maker_client):
        for _ in range(HISTORY_PAGE + 1):
            KilnTicketFactory(maker=maker, status="fired", fired_at=timezone.now())

        first = _pane(_page(maker_client))
        everything = _pane(_page(maker_client, query="?tab=kiln&older=1"))

        assert first.count("data-ticket=") == HISTORY_PAGE
        assert f'href="{kiln_home_url(older=True).replace("&", "&amp;")}"' in first
        assert everything.count("data-ticket=") == HISTORY_PAGE + 1
        assert "Show older tickets" not in everything

    def it_gives_a_maker_no_crew_links(kiln_guild, maker_client):
        pane = _pane(_page(maker_client))

        assert "data-kiln-crew-links" not in pane
        assert reverse("kiln:load") not in pane

    def it_gives_the_crew_their_pages_with_counts(kiln_guild, crew, crew_client):
        KilnTicketFactory(status="submitted", submitted_at=timezone.now())
        KilnTicketFactory(status="submitted", submitted_at=timezone.now())
        _firing(crew)

        pane = _pane(_page(crew_client))

        for name in ("kiln:load", "kiln:unload_list", "kiln:log", "kiln:lists"):
            assert f'href="{reverse(name)}"' in pane, name
        assert 'aria-label="2 waiting"' in pane
        assert 'aria-label="1 in the kiln"' in pane
        assert "Clay and glaze lists" in pane

    def it_gives_a_site_admin_the_crew_links(kiln_guild, make_member):
        admin = make_member(fog_role=Member.FogRole.ADMIN)
        admin.sync_user_permissions()

        assert "data-kiln-crew-links" in _pane(_page(signed_in(admin)))

    def it_costs_the_same_queries_for_one_ticket_or_fifteen(kiln_guild, crew, crew_client):
        def _ticket(status: str) -> None:
            ticket = KilnTicketFactory(maker=crew, status=status, submitted_at=timezone.now(), fired_at=timezone.now())
            KilnTicketPhotoFactory(ticket=ticket, is_cover=True)

        def _queries() -> int:
            _page(crew_client)
            with CaptureQueriesContext(connection) as captured:
                _page(crew_client)
            return len(captured.captured_queries)

        _ticket(KilnTicket.Status.SUBMITTED)
        one = _queries()
        for status in [KilnTicket.Status.DRAFT, KilnTicket.Status.SUBMITTED, KilnTicket.Status.FIRED] * 5:
            _ticket(status)

        assert _queries() == one


def describe_kiln_home_redirect():
    @pytest.mark.parametrize("role", ["member", "crew", "admin"])
    def it_sends_members_crew_and_admins_to_the_guild_tab(kiln_guild, make_member, role):
        member = make_member(fog_role=Member.FogRole.ADMIN) if role == "admin" else make_member()
        if role == "crew":
            from tests.membership.factories import GuildStaffMembershipFactory

            GuildStaffMembershipFactory(guild=kiln_guild, member=member)
        client = signed_in(member)

        response = client.get(MINE)

        assert response.status_code == 302
        assert response["Location"] == f"{reverse('hub_guild_detail', args=['ceramics-guild'])}?tab=kiln"
        assert client.get(f"{MINE}?older=1")["Location"] == kiln_home_url(older=True)
        assert TAB in client.get(response["Location"]).content.decode()

    def it_serves_a_guest_their_own_page(kiln_guild, make_member):
        guest = make_member(status=Member.Status.GUEST)
        ticket = KilnTicketFactory(maker=guest)

        response = signed_in(guest).get(MINE)

        assert response.status_code == 200
        assert f'data-ticket="{ticket.pk}"' in response.content.decode()
        assert response.context["home"].runs_kiln is False

    def it_lands_a_saved_draft_on_the_guild_tab(kiln_guild, maker_client):
        response = maker_client.post(reverse("kiln:new"), {"action": "draft", "quantity": "1"})

        assert response["Location"] == kiln_home_url()


def describe_the_way_back():
    def it_leads_every_kiln_page_back_to_the_ceramics_guild(kiln_guild, crew, crew_client):
        ticket = KilnTicketFactory(maker=crew)
        firing = _firing(crew)
        home = f'<a href="{kiln_home_url()}" data-kiln-home>Ceramics Guild</a>'

        maker_pages = [
            reverse("kiln:new"),
            reverse("kiln:detail", args=[ticket.pk]),
            reverse("kiln:edit", args=[ticket.pk]),
        ]
        crew_pages = [
            reverse("kiln:load"),
            reverse("kiln:unload_list"),
            reverse("kiln:unload", args=[firing.pk]),
            reverse("kiln:log"),
            reverse("kiln:lists"),
            reverse("kiln:firing", args=[firing.pk]),
        ]

        for url in maker_pages + crew_pages:
            assert home in crew_client.get(url).content.decode(), url
        for url in crew_pages:
            assert f'<a href="{kiln_home_url()}" class="vote-tab' in crew_client.get(url).content.decode(), url

    def it_leads_a_maker_back_from_their_ticket(kiln_guild, maker, maker_client):
        ticket = KilnTicketFactory(maker=maker)

        assert (
            f'href="{kiln_home_url()}" data-kiln-home'
            in maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()
        )

    def it_leads_a_guest_back_to_their_own_page(make_member):
        guest = make_member(status=Member.Status.GUEST)
        ticket = KilnTicketFactory(maker=guest)

        body = signed_in(guest).get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert f'<a href="{MINE}" data-kiln-home>Kiln Tickets</a>' in body
