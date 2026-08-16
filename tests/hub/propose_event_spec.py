"""BDD specs for the member "Propose an event" flow: policy branches on create, the
owner-scoped edit/resubmit loop, the "Your proposed events" surface, and withdraw."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory


def _member(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pass")


def _set_policy(value: str) -> None:
    config = SiteConfiguration.load()
    config.member_event_policy = value
    config.save()


def _payload(**overrides: str) -> dict:
    data = {
        "title": "My Event",
        "starts_at": "2026-08-01T18:00",
        "ends_at": "2026-08-01T20:00",
        "location": "",
        "description": "",
        "recurrence": "none",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def describe_create():
    def it_redirects_an_anonymous_user_to_login(client: Client):
        resp = client.get(reverse("hub_propose_event"))
        assert resp.status_code == 302
        assert "login" in resp["Location"]

    def it_403s_when_the_policy_is_disabled(client: Client):
        _member("d1")
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        client.login(username="d1", password="pass")
        assert client.get(reverse("hub_propose_event")).status_code == 403
        assert client.post(reverse("hub_propose_event"), data=_payload()).status_code == 403

    def it_submits_for_review_under_the_approval_policy(client: Client):
        user = _member("a1")
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        client.login(username="a1", password="pass")
        resp = client.post(reverse("hub_propose_event"), data=_payload(title="Approval Event"))
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Approval Event")
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.submitted_by == user
        assert event.is_site_wide

    def it_renders_the_video_link_field(client: Client):
        _member("vidf1")
        _set_policy(SiteConfiguration.MemberEventPolicy.OPEN)
        client.login(username="vidf1", password="pass")
        resp = client.get(reverse("hub_propose_event"))
        assert b"Video link" in resp.content

    def it_saves_a_video_url_on_the_proposal(client: Client):
        _member("vidf2")
        _set_policy(SiteConfiguration.MemberEventPolicy.OPEN)
        client.login(username="vidf2", password="pass")
        client.post(
            reverse("hub_propose_event"),
            data=_payload(title="Streamed Proposal", video_url="https://meet.google.com/x"),
        )
        event = CommunityEvent.objects.get(title="Streamed Proposal")
        assert event.video_url == "https://meet.google.com/x"

    def it_publishes_immediately_under_the_open_policy(client: Client):
        _member("o1")
        _set_policy(SiteConfiguration.MemberEventPolicy.OPEN)
        client.login(username="o1", password="pass")
        resp = client.post(reverse("hub_propose_event"), data=_payload(title="Open Event"))
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Open Event")
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.sync_state == CommunityEvent.SyncState.PENDING

    def it_derives_a_guild_meeting_when_a_guild_is_picked(client: Client):
        _member("g1")
        guild = GuildFactory()
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        client.login(username="g1", password="pass")
        resp = client.post(reverse("hub_propose_event"), data=_payload(title="Guild Event", guild=str(guild.pk)))
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Guild Event")
        assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert event.guild == guild

    def it_re_renders_with_errors_on_an_invalid_submission(client: Client):
        _member("inv1")
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        client.login(username="inv1", password="pass")
        # End before start → form error, page re-renders (200), nothing created.
        resp = client.post(
            reverse("hub_propose_event"),
            data=_payload(title="Bad", starts_at="2026-08-01T20:00", ends_at="2026-08-01T18:00"),
        )
        assert resp.status_code == 200
        assert not CommunityEvent.objects.filter(title="Bad").exists()


@pytest.mark.django_db
def describe_edit():
    def it_lets_the_owner_edit_a_changes_requested_proposal_and_resubmit(client: Client):
        user = _member("e1")
        event = CommunityEventFactory(
            community=True,
            moderation_state=CommunityEvent.ModerationState.CHANGES_REQUESTED,
            submitted_by=user,
            review_notes="Please add a description.",
        )
        client.login(username="e1", password="pass")
        get_resp = client.get(reverse("hub_propose_event_edit", args=[event.pk]))
        assert get_resp.status_code == 200
        assert b"A reviewer asked for changes" in get_resp.content
        assert b"Please add a description." in get_resp.content

        post_resp = client.post(
            reverse("hub_propose_event_edit", args=[event.pk]),
            data=_payload(title="Fixed", description="Now with detail"),
        )
        assert post_resp.status_code == 302
        event.refresh_from_db()
        assert event.title == "Fixed"
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.review_notes == ""

    def it_404s_editing_another_members_proposal(client: Client):
        _member("e2")
        other = _member("e2other")
        event = CommunityEventFactory(community=True, pending=True, submitted_by=other)
        client.login(username="e2", password="pass")
        assert client.get(reverse("hub_propose_event_edit", args=[event.pk])).status_code == 404

    def it_404s_editing_an_already_published_or_declined_event(client: Client):
        user = _member("e3")
        published = CommunityEventFactory(community=True, submitted_by=user)  # PUBLISHED
        declined = CommunityEventFactory(community=True, declined=True, submitted_by=user)
        client.login(username="e3", password="pass")
        assert client.get(reverse("hub_propose_event_edit", args=[published.pk])).status_code == 404
        assert client.get(reverse("hub_propose_event_edit", args=[declined.pk])).status_code == 404


@pytest.mark.django_db
def describe_my_proposals_surface():
    def it_shows_the_members_own_non_published_proposals_with_status(client: Client):
        user = _member("mp1")
        CommunityEventFactory(community=True, pending=True, submitted_by=user, title="Mine Pending")
        client.login(username="mp1", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert b"Mine Pending" in resp.content
        assert b"Pending review" in resp.content

    def it_does_not_show_another_members_proposals(client: Client):
        _member("mp2")
        other = _member("mp2other")
        CommunityEventFactory(community=True, pending=True, submitted_by=other, title="Someone Elses")
        client.login(username="mp2", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert b"Someone Elses" not in resp.content

    def it_shows_the_empty_state_on_the_propose_page(client: Client):
        _member("mp3")
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        client.login(username="mp3", password="pass")
        resp = client.get(reverse("hub_propose_event"))
        assert b"You haven't proposed any events yet." in resp.content


@pytest.mark.django_db
def describe_withdraw():
    def it_deletes_the_owners_pending_proposal(client: Client):
        user = _member("w1")
        event = CommunityEventFactory(community=True, pending=True, submitted_by=user)
        client.login(username="w1", password="pass")
        resp = client.post(reverse("hub_event_withdraw", args=[event.pk]))
        assert resp.status_code == 302
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()

    def it_404s_a_non_owner(client: Client):
        _member("w2")
        other = _member("w2other")
        event = CommunityEventFactory(community=True, pending=True, submitted_by=other)
        client.login(username="w2", password="pass")
        assert client.post(reverse("hub_event_withdraw", args=[event.pk])).status_code == 404
        assert CommunityEvent.objects.filter(pk=event.pk).exists()

    def it_404s_on_a_published_event(client: Client):
        user = _member("w3")
        event = CommunityEventFactory(community=True, submitted_by=user)  # PUBLISHED
        client.login(username="w3", password="pass")
        assert client.post(reverse("hub_event_withdraw", args=[event.pk])).status_code == 404

    def it_rejects_a_get(client: Client):
        user = _member("w4")
        event = CommunityEventFactory(community=True, pending=True, submitted_by=user)
        client.login(username="w4", password="pass")
        assert client.get(reverse("hub_event_withdraw", args=[event.pk])).status_code == 405
