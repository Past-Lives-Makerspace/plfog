"""The kiln launch switch (#691): off until the crew screens ship, then today's behavior.

``SiteConfiguration.kiln_tickets_open`` defaults off. Off: only the kiln crew and site admins
reach /kiln/ and see the Ceramics Guild's Kiln Tickets tab; everyone else gets a 404 there,
and guests are locked out exactly as before #691. On: members and guests file tickets.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from kiln.access import can_reach_kiln, can_run_kiln, is_crew, kiln_home_url, kiln_is_open
from membership.models import Member
from tests.kiln.conftest import set_kiln_open, signed_in
from tests.kiln.factories import KilnTicketFactory
from tests.membership.factories import GuildMembershipFactory

pytestmark = pytest.mark.django_db

MINE = reverse("kiln:mine")
GUEST = Member.Status.GUEST
LOCKED_GUEST = f"{reverse('account_locked')}?reason=guest"


@pytest.fixture
def closed() -> None:
    set_kiln_open(False)


def _urls(ticket_pk: int) -> list[str]:
    """Every maker page but /kiln/, which sends anyone but a guest to the guild tab."""
    return [
        reverse("kiln:new"),
        reverse("kiln:detail", args=[ticket_pk]),
        reverse("kiln:edit", args=[ticket_pk]),
        reverse("kiln:lists"),
    ]


def _has_tab(client: Client, guild_slug: str) -> bool:
    return 'data-guild-tab="kiln"' in client.get(reverse("hub_guild_detail", args=[guild_slug])).content.decode()


def _sign_in(client: Client, email: str):
    cache.clear()
    with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="ABCDEF"):
        client.post(reverse("account_request_login_code"), {"email": email})
    return client.post(reverse("account_confirm_login_code"), {"code": "ABCDEF"})


def describe_the_switch():
    def it_is_off_by_default_and_editable_with_the_site_settings():
        from hub.forms import SiteSettingsForm

        SiteConfiguration.objects.all().delete()

        assert SiteConfiguration.load().kiln_tickets_open is False
        assert kiln_is_open() is False
        assert "kiln_tickets_open" in SiteSettingsForm.base_fields

    def it_shows_on_the_site_settings_page(make_member):
        admin = make_member(fog_role=Member.FogRole.ADMIN)
        admin.sync_user_permissions()

        body = signed_in(admin).get(reverse("hub_admin_site_settings")).content.decode()

        assert 'name="kiln_tickets_open"' in body
        assert "Open kiln tickets to everyone" in body


def describe_while_closed():
    def it_lets_the_crew_in_everywhere(closed, crew, crew_client):
        ticket = KilnTicketFactory(maker=crew)

        for url in _urls(ticket.pk):
            assert crew_client.get(url).status_code == 200, url
        assert crew_client.get(MINE)["Location"] == kiln_home_url()
        assert can_reach_kiln(crew)

    def it_lets_a_site_admin_in_everywhere_with_the_crew_screens(closed, kiln_guild, make_member, maker):
        admin = make_member(fog_role=Member.FogRole.ADMIN)
        admin.sync_user_permissions()
        client = signed_in(admin)
        ticket = KilnTicketFactory(maker=maker)

        for url in [*_urls(ticket.pk)[:2], reverse("kiln:lists"), reverse("kiln:load"), reverse("kiln:log")]:
            assert client.get(url).status_code == 200, url
        assert can_reach_kiln(admin) and can_run_kiln(admin)
        assert not is_crew(admin)  # crew notifications stay with the lead and staff
        assert _has_tab(client, kiln_guild.slug)

    def it_keeps_the_crew_screens_from_a_guild_officer(closed, kiln_guild, make_member):
        officer = make_member(fog_role=Member.FogRole.GUILD_OFFICER)

        assert not can_run_kiln(officer)
        assert not can_run_kiln(None)

    def it_gives_a_member_a_404_on_every_kiln_url(closed, kiln_guild, maker, maker_client):
        GuildMembershipFactory(guild=kiln_guild, member=maker)
        ticket = KilnTicketFactory(maker=maker)

        for url in [MINE, *_urls(ticket.pk)]:
            assert maker_client.get(url).status_code == 404, url
        assert maker_client.post(reverse("kiln:list_add", args=["clay"]), {"name": "X"}).status_code == 404
        assert not can_reach_kiln(maker)

    def it_hides_the_guild_tab_from_members_but_not_the_crew(closed, kiln_guild, maker, crew):
        GuildMembershipFactory(guild=kiln_guild, member=maker)

        assert not _has_tab(signed_in(maker), kiln_guild.slug)
        assert _has_tab(signed_in(crew), kiln_guild.slug)

    def it_locks_a_guest_out_as_before(closed, make_member):
        client = signed_in(make_member(status=GUEST))

        for path in (MINE, reverse("hub_home")):
            response = client.get(path)
            assert response.status_code == 302 and response["Location"] == LOCKED_GUEST, path

    def it_refuses_a_guest_sign_in_as_before(closed, make_member):
        guest = make_member(status=GUEST)
        client = Client()

        response = _sign_in(client, guest.user.email)

        assert response["Location"] == LOCKED_GUEST
        assert client.session.get("_auth_user_id") is None


def describe_while_open():
    def it_lets_a_member_in_and_shows_them_the_guild_tab(kiln_guild, maker, maker_client):
        GuildMembershipFactory(guild=kiln_guild, member=maker)

        assert maker_client.get(MINE)["Location"] == kiln_home_url()
        assert maker_client.get(reverse("kiln:new")).status_code == 200
        assert _has_tab(maker_client, kiln_guild.slug)

    def it_lets_a_guest_sign_in_and_reach_only_the_kiln(make_member):
        guest = make_member(status=GUEST)
        client = Client()

        response = _sign_in(client, guest.user.email)

        assert client.session.get("_auth_user_id") == str(guest.user.pk)
        assert client.get(response["Location"], follow=True).redirect_chain[-1][0] == MINE
        assert client.get(MINE).status_code == 200
        assert client.get(reverse("hub_member_directory"))["Location"] == MINE
