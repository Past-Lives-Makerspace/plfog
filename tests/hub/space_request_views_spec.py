"""BDD specs for the member-facing request flow (create / withdraw, with its
dup-pending and membership guards and the three-surface pending swap) and the reviewer
queue (scope, decisions, the required-note re-open, and the stale-decision guard)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import MapHotspot, Member, Space, SpaceRequest
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MapHotspotFactory,
    MembershipPlanFactory,
    SpaceFactory,
    SpaceRequestFactory,
)


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.preferred_name = username.title()
    member.save(update_fields=["fog_role", "status", "preferred_name"])
    member.sync_user_permissions()
    return user


def _unlinked_user(username: str) -> User:
    """An account with no Member row — a bare superuser, say. Auto-provisioning would
    normally create one, so the row is removed to reach the view's own guard."""
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    Member.objects.filter(user=user).delete()
    return user


@pytest.mark.django_db
def describe_space_request_create():
    def it_redirects_an_anonymous_visitor_to_login(client: Client):
        hotspot = MapHotspotFactory()
        response = client.post(reverse("hub_space_request_create", args=[hotspot.pk]))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_creates_a_pending_request_and_toasts(client: Client):
        user = _user_with_role("asker")
        hotspot = MapHotspotFactory()
        client.login(username="asker", password="pass")
        response = client.post(reverse("hub_space_request_create", args=[hotspot.pk]), {"message": "For pottery."})
        assert response.status_code == 200
        assert "HX-Trigger" in response
        request = SpaceRequest.objects.get()
        assert request.requester == user.member
        assert request.space == hotspot.space
        assert request.hotspot == hotspot
        assert request.kind == SpaceRequest.RequestKind.LEASE
        assert request.message == "For pottery."

    def it_records_a_cubby_ask_as_a_cubby(client: Client):
        _user_with_role("asker-cubby")
        guild = GuildFactory()
        hotspot = MapHotspotFactory(kind=MapHotspot.Kind.CUBBY, space=SpaceFactory(sublet_guild=guild))
        client.login(username="asker-cubby", password="pass")
        client.post(reverse("hub_space_request_create", args=[hotspot.pk]), {"message": ""})
        assert SpaceRequest.objects.get().kind == SpaceRequest.RequestKind.CUBBY

    def it_swaps_the_marker_and_the_list_row_so_all_three_surfaces_agree(client: Client):
        _user_with_role("asker2")
        hotspot = MapHotspotFactory()
        client.login(username="asker2", password="pass")
        body = client.post(reverse("hub_space_request_create", args=[hotspot.pk]), {"message": ""}).content.decode()
        assert 'id="hotspot-%d" hx-swap-oob="true"' % hotspot.pk in body
        assert 'id="list-row-hotspot-%d" hx-swap-oob="true"' % hotspot.pk in body
        assert body.count("Request pending") >= 2

    def it_refuses_a_second_pending_request_with_a_friendly_message(client: Client):
        user = _user_with_role("asker3")
        hotspot = MapHotspotFactory()
        SpaceRequestFactory(requester=user.member, space=hotspot.space)
        client.login(username="asker3", password="pass")
        response = client.post(reverse("hub_space_request_create", args=[hotspot.pk]), {"message": ""})
        assert response.status_code == 200
        assert b"already have a pending request" in response.content
        assert SpaceRequest.objects.count() == 1

    def it_refuses_a_member_without_an_active_membership(client: Client):
        user = _user_with_role("lapsed")
        user.member.status = Member.Status.FORMER
        user.member.save(update_fields=["status"])
        hotspot = MapHotspotFactory()
        client.login(username="lapsed", password="pass")
        response = client.post(reverse("hub_space_request_create", args=[hotspot.pk]), {"message": ""})
        assert b"needs an active membership" in response.content
        assert SpaceRequest.objects.count() == 0

    def it_refuses_a_space_that_is_not_available(client: Client):
        _user_with_role("asker4")
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.OCCUPIED))
        client.login(username="asker4", password="pass")
        response = client.post(reverse("hub_space_request_create", args=[hotspot.pk]), {"message": ""})
        assert b"isn&#x27;t open for requests" in response.content
        assert SpaceRequest.objects.count() == 0

    def it_403s_a_login_with_no_member_record(client: Client):
        # An account can exist without a Member row (a bare superuser, say) — that
        # request has nobody to attribute, so it stops at the view rather than 500ing.
        hotspot = MapHotspotFactory()
        _unlinked_user("ghost")
        client.login(username="ghost", password="pass")
        response = client.post(reverse("hub_space_request_create", args=[hotspot.pk]), {"message": ""})
        assert response.status_code == 403

    def it_rejects_a_get(client: Client):
        _user_with_role("asker5")
        hotspot = MapHotspotFactory()
        client.login(username="asker5", password="pass")
        assert client.get(reverse("hub_space_request_create", args=[hotspot.pk])).status_code == 405


@pytest.mark.django_db
def describe_space_request_withdraw():
    def it_withdraws_the_members_own_request(client: Client):
        user = _user_with_role("wd")
        request = SpaceRequestFactory(requester=user.member)
        client.login(username="wd", password="pass")
        response = client.post(reverse("hub_space_request_withdraw", args=[request.pk]))
        assert response.status_code == 302
        request.refresh_from_db()
        assert request.state == SpaceRequest.ModerationState.WITHDRAWN

    def it_403s_a_login_with_no_member_record(client: Client):
        request = SpaceRequestFactory()
        _unlinked_user("ghost2")
        client.login(username="ghost2", password="pass")
        assert client.post(reverse("hub_space_request_withdraw", args=[request.pk])).status_code == 403

    def it_404s_someone_elses_request(client: Client):
        _user_with_role("wd2")
        request = SpaceRequestFactory()
        client.login(username="wd2", password="pass")
        assert client.post(reverse("hub_space_request_withdraw", args=[request.pk])).status_code == 404

    def it_reports_an_already_handled_request_rather_than_erroring(client: Client):
        user = _user_with_role("wd3")
        request = SpaceRequestFactory(requester=user.member, state=SpaceRequest.ModerationState.APPROVED)
        client.login(username="wd3", password="pass")
        response = client.post(reverse("hub_space_request_withdraw", args=[request.pk]), follow=True)
        assert b"already handled" in response.content


@pytest.mark.django_db
def describe_review_queue():
    def it_403s_a_plain_member(client: Client):
        _user_with_role("pm-q")
        client.login(username="pm-q", password="pass")
        assert client.get(reverse("hub_space_request_review_queue")).status_code == 403

    def it_shows_an_admin_every_lease_and_cubby_ask(client: Client):
        _user_with_role("adm-q", fog_role=Member.FogRole.ADMIN)
        SpaceRequestFactory(space=SpaceFactory(space_id="A9"))
        SpaceRequestFactory(space=SpaceFactory(space_id="C12"), kind=SpaceRequest.RequestKind.CUBBY)
        client.login(username="adm-q", password="pass")
        response = client.get(reverse("hub_space_request_review_queue"))
        assert response.status_code == 200
        assert b"A9" in response.content
        assert b"C12" in response.content

    def it_scopes_a_lead_to_their_own_guilds_cubbies(client: Client):
        user = _user_with_role("lead-q")
        mine = GuildFactory()
        GuildStaffMembershipFactory(guild=mine, member=user.member)
        theirs = GuildFactory()
        SpaceRequestFactory(space=SpaceFactory(space_id="MINE", sublet_guild=mine), kind=SpaceRequest.RequestKind.CUBBY)
        SpaceRequestFactory(
            space=SpaceFactory(space_id="THEIRS", sublet_guild=theirs), kind=SpaceRequest.RequestKind.CUBBY
        )
        SpaceRequestFactory(space=SpaceFactory(space_id="STUDIO", sublet_guild=mine))
        client.login(username="lead-q", password="pass")
        response = client.get(reverse("hub_space_request_review_queue"))
        assert b"MINE" in response.content
        assert b"THEIRS" not in response.content
        assert b"STUDIO" not in response.content

    def it_shows_an_empty_state(client: Client):
        _user_with_role("adm-q2", fog_role=Member.FogRole.ADMIN)
        client.login(username="adm-q2", password="pass")
        assert b"Nothing awaiting review." in client.get(reverse("hub_space_request_review_queue")).content


@pytest.mark.django_db
def describe_review_decision():
    def it_403s_a_plain_member(client: Client):
        _user_with_role("pm-d")
        request = SpaceRequestFactory()
        client.login(username="pm-d", password="pass")
        url = reverse("hub_space_request_review_decision", args=[request.pk])
        assert client.post(url, {"decision": "approve"}).status_code == 403

    def it_approves_from_the_query_string(client: Client):
        _user_with_role("adm-d", fog_role=Member.FogRole.ADMIN)
        request = SpaceRequestFactory()
        client.login(username="adm-d", password="pass")
        url = reverse("hub_space_request_review_decision", args=[request.pk])
        response = client.post(f"{url}?decision=approve")
        assert response.status_code == 302
        request.refresh_from_db()
        assert request.state == SpaceRequest.ModerationState.APPROVED

    def it_declines_with_a_note(client: Client):
        _user_with_role("adm-d2", fog_role=Member.FogRole.ADMIN)
        request = SpaceRequestFactory()
        client.login(username="adm-d2", password="pass")
        url = reverse("hub_space_request_review_decision", args=[request.pk])
        client.post(url, {"decision": "decline", "notes": "Already spoken for."})
        request.refresh_from_db()
        assert request.state == SpaceRequest.ModerationState.DECLINED
        assert request.review_notes == "Already spoken for."

    def it_reopens_the_modal_when_the_decline_note_is_blank(client: Client):
        _user_with_role("adm-d3", fog_role=Member.FogRole.ADMIN)
        request = SpaceRequestFactory()
        client.login(username="adm-d3", password="pass")
        url = reverse("hub_space_request_review_decision", args=[request.pk])
        response = client.post(url, {"decision": "decline", "notes": "   "})
        assert response.status_code == 200
        assert response.context["open_decision_for"] == request.pk
        assert b"Add a note so the member knows why." in response.content
        request.refresh_from_db()
        assert request.state == SpaceRequest.ModerationState.PENDING

    def it_reports_a_stale_decision_as_already_handled(client: Client):
        _user_with_role("adm-d4", fog_role=Member.FogRole.ADMIN)
        request = SpaceRequestFactory(state=SpaceRequest.ModerationState.APPROVED)
        client.login(username="adm-d4", password="pass")
        url = reverse("hub_space_request_review_decision", args=[request.pk])
        response = client.post(f"{url}?decision=approve", follow=True)
        assert b"already handled" in response.content

    def it_404s_a_request_outside_a_leads_scope(client: Client):
        user = _user_with_role("lead-d")
        GuildStaffMembershipFactory(guild=GuildFactory(), member=user.member)
        request = SpaceRequestFactory()
        client.login(username="lead-d", password="pass")
        url = reverse("hub_space_request_review_decision", args=[request.pk])
        assert client.post(f"{url}?decision=approve").status_code == 404
