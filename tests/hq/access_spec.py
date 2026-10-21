"""Who may open the HQ and its cards (Track C, part 1): the audience matrix, hidden cards,
a closed hub, the login redirect, the organiser gate and where /hq/ sends each viewer."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from hq import access
from hq.access import SignInRequired, card_or_404, card_view, hub_or_404, organizer_required
from hq.models import HubCard
from hub.view_as import ROLE_GUEST, ROLE_MEMBER, ViewAs
from membership.models import Member
from tests.hq.conftest import signed_in
from tests.hq.factories import EventHubFactory, HubCardFactory

pytestmark = pytest.mark.django_db

PUBLIC = HubCard.Audience.PUBLIC
MEMBERS = HubCard.Audience.MEMBERS
ADMINS = HubCard.Audience.ADMINS
GUEST = Member.Status.GUEST


def _page(hub: Any) -> str:
    return reverse("hq:page", args=[hub.slug])


def _marker(card: Any) -> str:
    return f'data-hq-card-pk="{card.pk}"'


def _request(user: Any, path: str = "/hq/", picked: str | None = None) -> HttpRequest:
    """A request with ``view_as`` set the way ViewAsMiddleware does, optionally previewing ``picked``."""
    request = RequestFactory().get(path)
    request.user = user
    actual = ViewAs.for_request(request).actual
    request.view_as = ViewAs(actual=actual, picked=picked)  # type: ignore[attr-defined]
    return request


@pytest.fixture
def doors(open_hub):
    """One door per audience, plus a hidden public one."""
    return {
        "public": HubCardFactory(hub=open_hub, audience=PUBLIC),
        "members": HubCardFactory(hub=open_hub, audience=MEMBERS),
        "admins": HubCardFactory(hub=open_hub, audience=ADMINS),
        "hidden": HubCardFactory(hub=open_hub, audience=PUBLIC, is_hidden=True),
    }


def describe_the_hq_page():
    @pytest.mark.parametrize(
        ("viewer", "sees"),
        [
            ("anonymous", {"public"}),
            ("guest", {"public"}),
            ("member", {"public", "members"}),
            ("named", {"public", "members", "admins"}),
            ("admin", {"public", "members", "admins", "hidden"}),
        ],
    )
    def it_shows_each_door_to_exactly_who_its_audience_names(make_member, open_hub, doors, viewer, sees):
        clients = {
            "anonymous": Client(),
            "guest": signed_in(make_member(status=GUEST)),
            "member": signed_in(make_member()),
            "admin": signed_in(make_member(fog_role=Member.FogRole.ADMIN)),
        }
        named = make_member()
        doors["admins"].named_members.add(named)
        clients["named"] = signed_in(named)

        body = clients[viewer].get(_page(open_hub)).content.decode()

        for key, card in doors.items():
            assert (_marker(card) in body) is (key in sees), (viewer, key)

    def it_marks_a_hidden_door_for_an_admin_and_never_links_it_for_anyone_else(admin_client, member_client, doors):
        admin_body = admin_client.get(_page(doors["hidden"].hub)).content.decode()
        member_body = member_client.get(_page(doors["hidden"].hub)).content.decode()

        assert "data-hq-hidden-chip" in admin_body
        assert _marker(doors["hidden"]) not in member_body
        assert "data-hq-hidden-chip" not in member_body

    def it_gives_edit_links_and_the_edit_action_to_admins_only(admin_client, member_client, doors):
        hub = doors["public"].hub

        admin_body = admin_client.get(_page(hub)).content.decode()
        member_body = member_client.get(_page(hub)).content.decode()

        assert "data-hq-edit" in admin_body
        assert reverse("hq:card_edit", args=[hub.slug, doors["public"].pk]) in admin_body
        assert "data-hq-edit" not in member_body
        assert "data-hq-card-edit" not in member_body

    def it_renders_what_a_previewed_role_would_see(admin_client, doors):
        """An admin previewing as Guest sees the public doors and no Edit; the actual role is untouched."""
        session = admin_client.session
        session["view_as_role"] = ROLE_GUEST
        session.save()

        body = admin_client.get(_page(doors["public"].hub)).content.decode()

        assert _marker(doors["public"]) in body
        assert _marker(doors["members"]) not in body
        assert _marker(doors["admins"]) not in body
        assert "data-hq-edit" not in body

    def it_keeps_a_members_door_from_an_admin_previewing_as_a_member_who_is_one(admin_client, doors):
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        body = admin_client.get(_page(doors["members"].hub)).content.decode()

        assert _marker(doors["members"]) in body
        assert _marker(doors["admins"]) not in body


def describe_a_closed_hub():
    def it_is_a_404_for_everyone_but_an_admin(make_member, admin_client, member_client):
        hub = EventHubFactory(is_open=False)

        assert Client().get(_page(hub)).status_code == 404
        assert member_client.get(_page(hub)).status_code == 404
        assert signed_in(make_member(status=GUEST)).get(_page(hub)).status_code == 404
        assert admin_client.get(_page(hub)).status_code == 200

    def it_is_a_404_for_an_admin_previewing_below_admin(admin_client):
        hub = EventHubFactory(is_open=False)
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        assert admin_client.get(_page(hub)).status_code == 404

    def it_404s_an_unknown_slug(admin_client):
        assert admin_client.get(reverse("hq:page", args=["nowhere"])).status_code == 404

    def it_resolves_through_hub_or_404(admin, member):
        hub = EventHubFactory(is_open=False)

        assert hub_or_404(hub.slug, _request(admin.user)) == hub
        with pytest.raises(Http404):
            hub_or_404(hub.slug, _request(member.user))
        with pytest.raises(Http404):
            hub_or_404("nowhere", _request(admin.user))


def describe_index():
    def it_sends_everyone_to_the_open_hub(open_hub, admin_client, member_client):
        for client in (Client(), member_client, admin_client):
            response = client.get(reverse("hq:index"))
            assert response.status_code == 302
            assert response["Location"] == _page(open_hub)

    def it_sends_an_admin_to_the_closed_hub_when_none_is_open(admin_client):
        closed = EventHubFactory(is_open=False)

        response = admin_client.get(reverse("hq:index"))

        assert response.status_code == 302
        assert response["Location"] == _page(closed)

    def it_sends_an_admin_to_load_lees_hq_when_there_is_none(admin_client):
        response = admin_client.get(reverse("hq:index"))

        assert response.status_code == 302
        assert response["Location"] == reverse("hq:setup")

    def it_404s_everyone_else_when_none_is_open(member_client, make_member):
        EventHubFactory(is_open=False)

        assert Client().get(reverse("hq:index")).status_code == 404
        assert member_client.get(reverse("hq:index")).status_code == 404
        assert signed_in(make_member(status=GUEST)).get(reverse("hq:index")).status_code == 404

    def it_404s_an_admin_previewing_as_a_member_when_none_is_open(admin_client):
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        assert admin_client.get(reverse("hq:index")).status_code == 404


def describe_card_or_404():
    def it_returns_a_public_card_to_a_signed_out_visitor(doors):
        card = card_or_404(doors["public"].hub, HubCard.Kind.PLACEHOLDER, _request(AnonymousUser()))

        assert card == doors["public"]

    def it_sends_a_signed_out_visitor_through_login_for_a_members_card(open_hub):
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS, audience=MEMBERS)

        with pytest.raises(SignInRequired):
            card_or_404(open_hub, HubCard.Kind.VOLUNTEERS, _request(AnonymousUser()))

    def it_404s_a_members_card_for_a_guest_account(open_hub, make_member):
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS, audience=MEMBERS)

        with pytest.raises(Http404):
            card_or_404(open_hub, HubCard.Kind.VOLUNTEERS, _request(make_member(status=GUEST).user))

    def it_returns_a_members_card_to_a_member(open_hub, member):
        card = HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS, audience=MEMBERS)

        assert card_or_404(open_hub, HubCard.Kind.VOLUNTEERS, _request(member.user)) == card

    def it_404s_an_admins_card_for_a_member_but_not_for_a_named_member(open_hub, member, make_member):
        named = make_member()
        card = HubCardFactory(hub=open_hub, kind=HubCard.Kind.WINE_WALL, audience=ADMINS, named=[named])

        with pytest.raises(Http404):
            card_or_404(open_hub, HubCard.Kind.WINE_WALL, _request(member.user))
        assert card_or_404(open_hub, HubCard.Kind.WINE_WALL, _request(named.user)) == card

    def it_404s_a_hidden_card_for_anyone_but_an_admin(open_hub, member, admin):
        card = HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS, audience=PUBLIC, is_hidden=True)

        with pytest.raises(Http404):
            card_or_404(open_hub, HubCard.Kind.VOLUNTEERS, _request(member.user))
        with pytest.raises(Http404):
            card_or_404(open_hub, HubCard.Kind.VOLUNTEERS, _request(AnonymousUser()))
        assert card_or_404(open_hub, HubCard.Kind.VOLUNTEERS, _request(admin.user)) == card

    def it_404s_a_kind_the_hub_does_not_have(open_hub, admin):
        with pytest.raises(Http404):
            card_or_404(open_hub, HubCard.Kind.WINE_WALL, _request(admin.user))

    def it_hands_an_admin_previewing_as_guest_what_a_guest_account_gets(open_hub, admin):
        """The preview is still a signed in request, so a members card is the guest account's 404, not the login page."""
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS, audience=MEMBERS)

        with pytest.raises(Http404):
            card_or_404(open_hub, HubCard.Kind.VOLUNTEERS, _request(admin.user, picked=ROLE_GUEST))


def describe_card_view():
    def _view(request: HttpRequest, *, hub: Any, card: Any) -> HttpResponse:
        return HttpResponse(f"{hub.slug}:{card.kind}")

    def it_hands_the_hub_and_card_to_the_view(open_hub, member):
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS, audience=MEMBERS)
        wrapped = card_view(HubCard.Kind.VOLUNTEERS)(_view)

        response = wrapped(_request(member.user), slug=open_hub.slug)

        assert response.content == f"{open_hub.slug}:volunteers".encode()

    def it_sends_a_signed_out_visitor_to_login_and_back(open_hub):
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS, audience=MEMBERS)
        wrapped = card_view(HubCard.Kind.VOLUNTEERS)(_view)
        path = f"/hq/{open_hub.slug}/volunteers/"

        response = wrapped(_request(AnonymousUser(), path=path), slug=open_hub.slug)

        assert response.status_code == 302
        assert response["Location"] == f"/accounts/login/?next={path}"


def describe_organizer_required():
    def _view(request: HttpRequest, *, hub: Any, card: Any) -> HttpResponse:
        return HttpResponse("ok")

    def it_admits_an_admin_by_actual_role_even_while_previewing(open_hub, admin):
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.WINE_WALL)
        wrapped = organizer_required(HubCard.Kind.WINE_WALL)(_view)

        assert wrapped(_request(admin.user, picked=ROLE_MEMBER), slug=open_hub.slug).status_code == 200

    def it_admits_an_actual_admin_to_a_closed_hub_while_previewing_as_a_member(admin):
        """Actions read the actual role, so a preview never hides an HQ an admin is still building."""
        closed = EventHubFactory(is_open=False)
        HubCardFactory(hub=closed, kind=HubCard.Kind.WINE_WALL)
        wrapped = organizer_required(HubCard.Kind.WINE_WALL)(_view)

        assert wrapped(_request(admin.user, picked=ROLE_MEMBER), slug=closed.slug).status_code == 200

    def it_keeps_a_closed_hub_from_a_named_member(make_member):
        named = make_member()
        closed = EventHubFactory(is_open=False)
        HubCardFactory(hub=closed, kind=HubCard.Kind.WINE_WALL, named=[named])
        wrapped = organizer_required(HubCard.Kind.WINE_WALL)(_view)

        with pytest.raises(Http404):
            wrapped(_request(named.user), slug=closed.slug)

    def it_admits_a_named_member(open_hub, make_member):
        named = make_member()
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.WINE_WALL, named=[named])
        wrapped = organizer_required(HubCard.Kind.WINE_WALL)(_view)

        assert wrapped(_request(named.user), slug=open_hub.slug).status_code == 200

    def it_refuses_anyone_else_with_a_403(open_hub, member):
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.WINE_WALL, audience=PUBLIC)
        wrapped = organizer_required(HubCard.Kind.WINE_WALL)(_view)

        with pytest.raises(PermissionDenied):
            wrapped(_request(member.user), slug=open_hub.slug)
        with pytest.raises(PermissionDenied):
            wrapped(_request(AnonymousUser()), slug=open_hub.slug)

    def it_404s_a_card_the_hub_does_not_have(open_hub, admin):
        wrapped = organizer_required(HubCard.Kind.WINE_WALL)(_view)

        with pytest.raises(Http404):
            wrapped(_request(admin.user), slug=open_hub.slug)


def describe_viewer_member():
    def it_is_nobody_signed_out_and_the_member_signed_in(member):
        assert access.viewer_member(_request(AnonymousUser())) is None
        assert access.viewer_member(_request(member.user)) == member

    def it_is_nobody_for_an_admin_previewing_as_guest_but_not_as_member(admin):
        assert access.viewer_member(_request(admin.user, picked=ROLE_GUEST)) is None
        assert access.viewer_member(_request(admin.user, picked=ROLE_MEMBER)) == admin

    def it_is_the_guest_account_itself_for_a_real_guest(make_member):
        guest = make_member(status=GUEST)

        assert access.viewer_member(_request(guest.user)) == guest
