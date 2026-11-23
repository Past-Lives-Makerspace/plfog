"""Who may open the HQ (#1307): admins and every ACTIVE guild officer run all of it; nobody else reaches any of it.

The rule (``hq.access.can_run_hq``), the one gate on every HQ URL (``hq_gate``), hidden cards, a closed hub, where
/hq/ sends each viewer, and the effective role for rendering against the actual role for acting.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404, HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.urls import URLPattern, reverse

from hq import access
from hq import urls as hq_urls
from hq.access import can_run_hq, hq_gate, officers
from hq.models import EventHub, HubCard
from hub.view_as import ROLE_GUEST, ROLE_MEMBER, ViewAs
from membership.models import Member
from tests.hq.conftest import signed_in
from tests.hq.factories import EventHubFactory, HubCardFactory
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db

LOGIN = "/accounts/login/"


def _page(hub: Any) -> str:
    return reverse("hq:page", args=[hub.slug])


def _marker(card: Any) -> str:
    return f'data-hq-card-pk="{card.pk}"'


def _request(user: Any, method: str = "get", picked: str | None = None) -> HttpRequest:
    """A request with ``view_as`` set the way ViewAsMiddleware does, optionally previewing ``picked``."""
    request = getattr(RequestFactory(), method)("/hq/spec/")
    request.user = user
    actual = ViewAs.for_request(request).actual
    request.view_as = ViewAs(actual=actual, picked=picked)  # type: ignore[attr-defined]
    return request


def _preview(client: Client, role: str) -> None:
    session = client.session
    session["view_as_role"] = role
    session.save()


def _every_hq_url(hub: EventHub) -> list[str]:
    """Every URL the hq app serves, for ``hub``: a missing pk is 1 and the flyer slot is this year's."""
    values = {"slug": hub.slug, "pk": 1, "shift_pk": 1, "slot": "flyer"}
    found = []
    for pattern in hq_urls.urlpatterns:
        assert isinstance(pattern, URLPattern)
        names = pattern.pattern.regex.groupindex
        found.append(reverse(f"hq:{pattern.name}", kwargs={name: values[name] for name in names}))
    return found


@pytest.fixture
def lees_hq() -> EventHub:
    """The open hub with all of Lee's doors on it, the way prod holds them."""
    hub = EventHubFactory(is_open=True, slug="holiday-bizarre-2026", title="Holiday Bizarre HQ")
    hub.seed_default_cards()
    return hub


def describe_can_run_hq():
    def it_admits_an_admin_with_or_without_a_member_record():
        assert can_run_hq(None, is_admin=True)

    def it_admits_an_active_guild_lead_and_an_active_guild_staff_member(make_officer):
        assert can_run_hq(make_officer(), is_admin=False)
        assert can_run_hq(make_officer(staff=True), is_admin=False)

    def it_refuses_a_member_who_leads_or_staffs_no_guild(member):
        assert not can_run_hq(member, is_admin=False)

    def it_refuses_signed_out(db):
        assert not can_run_hq(None, is_admin=False)

    @pytest.mark.parametrize(
        "status", [Member.Status.GUEST, Member.Status.INVITED, Member.Status.FORMER, Member.Status.SUSPENDED]
    )
    def it_refuses_a_guild_officer_who_is_not_active(make_officer, status):
        assert not can_run_hq(make_officer(status=status), is_admin=False)


def describe_officers():
    def it_lists_every_active_lead_and_staff_member_once(make_officer, make_member, member):
        lead = make_officer(preferred_name="Kate")
        staff = make_officer(staff=True, preferred_name="Sam")
        GuildFactory(guild_lead=lead)  # leads two guilds, still listed once
        GuildStaffMembershipFactory(member=lead)
        make_officer(status=Member.Status.FORMER)

        assert list(officers()) == [lead, staff]


def describe_the_hq_page():
    def it_gives_a_guild_lead_every_card_with_edit_and_edit_hq(lees_hq, officer_client):
        response = officer_client.get(_page(lees_hq))

        assert response.status_code == 200
        body = response.content.decode()
        for card in lees_hq.cards.all():
            assert _marker(card) in body
            assert reverse("hq:card_edit", args=[lees_hq.slug, card.pk]) in body
        assert "data-hq-edit" in body
        assert "data-hq-card-share" not in body
        assert "Admins only" not in body

    def it_opens_every_door_the_hq_page_links_for_a_guild_lead(lees_hq, officer_client):
        urls = [card.url for card in lees_hq.cards.all() if card.url]

        assert len(urls) == lees_hq.cards.count() - 1  # the RSVP door is blank with no event linked
        for url in urls:
            assert officer_client.get(url).status_code == 200, url

    def it_gives_a_guild_staff_member_the_same_page(lees_hq, make_officer):
        assert signed_in(make_officer(staff=True)).get(_page(lees_hq)).status_code == 200

    def it_is_a_404_for_a_member_who_leads_or_staffs_no_guild(lees_hq, member_client):
        assert member_client.get(_page(lees_hq)).status_code == 404

    def it_sends_a_signed_out_visitor_through_login_and_back(lees_hq):
        response = Client().get(_page(lees_hq))

        assert response.status_code == 302
        assert response["Location"].startswith(LOGIN)
        assert _page(lees_hq) in response["Location"]

    def it_takes_the_hq_away_on_the_next_request_once_they_stop_leading(lees_hq, officer, officer_client):
        assert officer_client.get(_page(lees_hq)).status_code == 200

        officer.led_guilds.update(guild_lead=None)

        assert officer_client.get(_page(lees_hq)).status_code == 404

    def it_shows_a_hidden_card_to_an_admin_with_its_chip_and_to_nobody_else(lees_hq, admin_client, officer_client):
        card = lees_hq.cards.get(kind=HubCard.Kind.LEDGER)
        card.is_hidden = True
        card.save(update_fields=["is_hidden"])

        admin_body = admin_client.get(_page(lees_hq)).content.decode()
        officer_body = officer_client.get(_page(lees_hq)).content.decode()

        assert _marker(card) in admin_body and "data-hq-hidden-chip" in admin_body
        assert _marker(card) not in officer_body
        assert officer_client.get(card.url).status_code == 404
        assert admin_client.get(card.url).status_code == 200

    @pytest.mark.parametrize("role", [ROLE_MEMBER, ROLE_GUEST])
    def it_shows_an_admin_previewing_below_admin_the_404_that_viewer_gets(lees_hq, admin_client, role):
        _preview(admin_client, role)

        assert admin_client.get(_page(lees_hq)).status_code == 404

    def it_lets_an_admin_who_is_also_an_officer_preview_as_a_member(lees_hq, make_officer):
        client = signed_in(make_officer(fog_role=Member.FogRole.ADMIN))
        _preview(client, ROLE_MEMBER)

        assert client.get(_page(lees_hq)).status_code == 200


def describe_every_hq_url():
    def it_is_a_404_for_a_member_who_is_no_officer_on_get_and_post(lees_hq, member_client):
        for url in _every_hq_url(lees_hq):
            assert member_client.get(url).status_code in {404, 405}, url
            assert member_client.post(url).status_code in {404, 405}, url

    def it_sends_a_signed_out_visitor_to_login_on_get_and_post(lees_hq):
        client = Client()
        for url in _every_hq_url(lees_hq):
            for response in (client.get(url), client.post(url)):
                if response.status_code == 405:
                    continue
                assert response.status_code == 302, url
                assert response["Location"].startswith(LOGIN), url

    def it_never_turns_an_officer_away(lees_hq, officer_client):
        for url in _every_hq_url(lees_hq):
            if "/1/" in url or "/download/" in url or "vendors/discord/" in url:
                continue  # rows and files that do not exist are a 404 for anyone; Check Discord reads Discord
            response = officer_client.get(url)

            assert response.status_code in {200, 302, 405}, url
            assert not response.get("Location", "").startswith(LOGIN), url


def describe_a_closed_hub():
    def it_opens_to_whoever_runs_the_hq_so_it_can_be_built_first(officer_client, admin_client, member_client):
        hub = EventHubFactory(is_open=False)

        assert officer_client.get(_page(hub)).status_code == 200
        assert admin_client.get(_page(hub)).status_code == 200
        assert member_client.get(_page(hub)).status_code == 404

    def it_404s_an_unknown_slug(officer_client):
        assert officer_client.get(reverse("hq:page", args=["no-such-hq"])).status_code == 404


def describe_index():
    def it_sends_whoever_runs_the_hq_to_the_open_hub(open_hub, officer_client, admin_client):
        for client in (officer_client, admin_client):
            response = client.get(reverse("hq:index"))

            assert response.status_code == 302
            assert response["Location"] == open_hub.get_absolute_url()

    def it_sends_them_to_the_newest_closed_hub_when_none_is_open(officer_client):
        EventHubFactory(is_open=False, slug="older")
        newest = EventHubFactory(is_open=False, slug="newer")

        assert officer_client.get(reverse("hq:index"))["Location"] == newest.get_absolute_url()

    def it_sends_them_to_load_lees_hq_when_there_is_none(officer_client):
        assert officer_client.get(reverse("hq:index"))["Location"] == reverse("hq:setup")

    def it_is_a_404_for_anyone_else(open_hub, member_client):
        assert member_client.get(reverse("hq:index")).status_code == 404


def _echo(request: HttpRequest, *, hub: Any, card: Any) -> HttpResponse:
    return HttpResponse(f"{hub.slug}:{card.kind}")


def describe_hq_gate():
    def it_hands_the_hub_and_its_card_to_the_view(open_hub, officer):
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.LEDGER)
        view = hq_gate(HubCard.Kind.LEDGER)(_echo)

        response = view(_request(officer.user), slug=open_hub.slug)

        assert response.content == f"{open_hub.slug}:ledger".encode()

    def it_404s_a_card_the_hub_does_not_have(open_hub, admin):
        view = hq_gate(HubCard.Kind.LEDGER)(lambda request, **kwargs: HttpResponse("ok"))

        with pytest.raises(Http404):
            view(_request(admin.user), slug=open_hub.slug)

    def it_reads_the_actual_role_to_act_and_the_effective_role_to_render(open_hub, admin):
        view = hq_gate()(lambda request, **kwargs: HttpResponse("ok"))

        assert view(_request(admin.user, method="post", picked=ROLE_GUEST)).status_code == 200
        with pytest.raises(Http404):
            view(_request(admin.user, picked=ROLE_GUEST))

    def it_keeps_a_hidden_card_from_an_officer_but_not_from_an_admin_acting_in_a_preview(open_hub, officer, admin):
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.LEDGER, is_hidden=True)
        view = hq_gate(HubCard.Kind.LEDGER)(lambda request, **kwargs: HttpResponse("ok"))

        with pytest.raises(Http404):
            view(_request(officer.user, method="post"), slug=open_hub.slug)
        assert view(_request(admin.user, method="post", picked=ROLE_MEMBER), slug=open_hub.slug).status_code == 200


def describe_viewer_member():
    def it_is_nobody_signed_out_and_the_member_signed_in(member):
        assert access.viewer_member(_request(AnonymousUser())) is None
        assert access.viewer_member(_request(member.user)) == member

    def it_is_nobody_for_an_admin_previewing_as_guest_but_not_as_member(admin):
        assert access.viewer_member(_request(admin.user, picked=ROLE_GUEST)) is None
        assert access.viewer_member(_request(admin.user, picked=ROLE_MEMBER)) == admin

    def it_is_nobody_acting_while_signed_out(db):
        assert access.actual_member(_request(AnonymousUser())) is None
