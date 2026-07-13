"""BDD specs for the reviewer queue + decision views: gating, guild-scoped visibility,
cross-guild isolation, the required-notes re-open-modal error state, and the changed
direct-create publish path."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _payload(**overrides: str) -> dict:
    data = {
        "title": "Forge Night",
        "starts_at": "2026-07-11T18:00",
        "ends_at": "2026-07-11T20:00",
        "location": "Main Studio",
        "description": "Come forge.",
        "recurrence": "none",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def describe_queue_gating():
    def it_403s_a_plain_member(client: Client):
        _user_with_role("pm1")
        client.login(username="pm1", password="pass")
        assert client.get(reverse("hub_event_review_queue")).status_code == 403

    def it_lets_an_admin_see_every_pending_proposal(client: Client):
        _user_with_role("adm1", fog_role=Member.FogRole.ADMIN)
        CommunityEventFactory(community=True, pending=True, title="Site Proposal")
        client.login(username="adm1", password="pass")
        resp = client.get(reverse("hub_event_review_queue"))
        assert resp.status_code == 200
        assert b"Site Proposal" in resp.content

    def it_scopes_a_lead_to_their_own_guilds(client: Client):
        user = _user_with_role("lead1")
        guild_a = GuildFactory(guild_lead=user.member)
        guild_b = GuildFactory()
        CommunityEventFactory(guild=guild_a, pending=True, title="Mine Pending")
        CommunityEventFactory(guild=guild_b, pending=True, title="Other Guild Pending")
        client.login(username="lead1", password="pass")
        resp = client.get(reverse("hub_event_review_queue"))
        assert resp.status_code == 200
        assert b"Mine Pending" in resp.content
        assert b"Other Guild Pending" not in resp.content
        assert b'id="event-' in resp.content  # anchor target for the review deep-link


@pytest.mark.django_db
def describe_decision():
    def it_approves_a_pending_event_via_the_query_string(client: Client):
        _user_with_role("adm2", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, pending=True)
        client.login(username="adm2", password="pass")
        resp = client.post(reverse("hub_event_review_decision", args=[event.pk]) + "?decision=approve")
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED

    def it_declines_with_a_note(client: Client):
        _user_with_role("adm3", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, pending=True)
        client.login(username="adm3", password="pass")
        resp = client.post(
            reverse("hub_event_review_decision", args=[event.pk]),
            data={"decision": "decline", "notes": "Not this time."},
        )
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.DECLINED
        assert event.review_notes == "Not this time."

    def it_requests_changes_with_a_note(client: Client):
        _user_with_role("adm4", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, pending=True)
        client.login(username="adm4", password="pass")
        resp = client.post(
            reverse("hub_event_review_decision", args=[event.pk]),
            data={"decision": "changes", "notes": "Move it later."},
        )
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.CHANGES_REQUESTED

    def it_re_renders_the_queue_with_an_error_when_a_decline_note_is_missing(client: Client):
        _user_with_role("adm5", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, pending=True)
        client.login(username="adm5", password="pass")
        resp = client.post(
            reverse("hub_event_review_decision", args=[event.pk]),
            data={"decision": "decline", "notes": ""},
        )
        assert resp.status_code == 200
        assert b"Add a note so the proposer knows why." in resp.content
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING  # unchanged

    def it_friendly_redirects_when_the_event_was_already_handled(client: Client):
        _user_with_role("adm6", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)  # already PUBLISHED
        client.login(username="adm6", password="pass")
        resp = client.post(reverse("hub_event_review_decision", args=[event.pk]) + "?decision=approve")
        assert resp.status_code == 302  # no 500
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED

    def it_rejects_a_get(client: Client):
        _user_with_role("adm7", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, pending=True)
        client.login(username="adm7", password="pass")
        assert client.get(reverse("hub_event_review_decision", args=[event.pk])).status_code == 405


@pytest.mark.django_db
def describe_cross_guild_isolation():
    def it_404s_a_lead_deciding_on_another_guilds_event(client: Client):
        user = _user_with_role("iso_lead")
        GuildFactory(guild_lead=user.member)
        guild_b = GuildFactory()
        b_event = CommunityEventFactory(guild=guild_b, pending=True)
        client.login(username="iso_lead", password="pass")
        resp = client.post(reverse("hub_event_review_decision", args=[b_event.pk]) + "?decision=approve")
        assert resp.status_code == 404
        b_event.refresh_from_db()
        assert b_event.moderation_state == CommunityEvent.ModerationState.PENDING


@pytest.mark.django_db
def describe_changed_direct_create():
    def it_publishes_a_lead_created_event(client: Client):
        user = _user_with_role("dc_lead")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="dc_lead", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(reverse("hub_guild_event_add", args=[guild.pk]), data=_payload())
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(guild=guild)
        # publish() ran: announced once AND flagged for a Google push.
        mock_announce.assert_called_once()
        assert event.sync_state == CommunityEvent.SyncState.PENDING
