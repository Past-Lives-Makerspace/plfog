from __future__ import annotations

from unittest.mock import patch

import pytest
from rest_framework import status

from membership.models import CommunityEvent, GuildAnnouncement, Member
from tests.membership.factories import (
    CommunityEventFactory,
    GuildAnnouncementFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def describe_MemberViewSet():
    def describe_list():
        def it_requires_authentication(api_client):
            response = api_client.get("/api/v1/members/")
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

        def it_requires_fog_admin(member_client):
            response = member_client.get("/api/v1/members/")
            assert response.status_code == status.HTTP_403_FORBIDDEN

        def it_returns_active_members(admin_client, admin_member):
            response = admin_client.get("/api/v1/members/")
            assert response.status_code == status.HTTP_200_OK
            ids = [r["id"] for r in response.data["results"]]
            assert admin_member.id in ids

        def it_excludes_non_active_members(admin_client):
            former = MemberFactory(status=Member.Status.FORMER)
            response = admin_client.get("/api/v1/members/")
            ids = [r["id"] for r in response.data["results"]]
            assert former.id not in ids

    def describe_retrieve():
        def it_requires_fog_admin(member_client, admin_member):
            response = member_client.get(f"/api/v1/members/{admin_member.id}/")
            assert response.status_code == status.HTTP_403_FORBIDDEN

        def it_returns_member_fields(admin_client, admin_member):
            response = admin_client.get(f"/api/v1/members/{admin_member.id}/")
            assert response.status_code == status.HTTP_200_OK
            assert response.data["id"] == admin_member.id
            assert "display_name" in response.data
            assert "primary_email" in response.data

    def describe_partial_update():
        def it_updates_notes(admin_client, admin_member):
            response = admin_client.patch(f"/api/v1/members/{admin_member.id}/", {"notes": "test note"})
            assert response.status_code == status.HTTP_200_OK
            admin_member.refresh_from_db()
            assert admin_member.notes == "test note"

    def describe_set_role():
        def it_requires_fog_admin(member_client, admin_member):
            response = member_client.post(f"/api/v1/members/{admin_member.id}/set-role/", {"role": "member"})
            assert response.status_code == status.HTTP_403_FORBIDDEN

        def it_calls_apply_admin_role(admin_client, admin_member):
            target = MemberFactory(fog_role=Member.FogRole.MEMBER)
            with patch.object(Member, "apply_admin_role") as mock_apply:
                response = admin_client.post(f"/api/v1/members/{target.id}/set-role/", {"role": "guild_officer"})
            assert response.status_code == status.HTTP_200_OK
            mock_apply.assert_called_once_with("guild_officer")

        def it_rejects_invalid_roles(admin_client, admin_member):
            response = admin_client.post(f"/api/v1/members/{admin_member.id}/set-role/", {"role": "supervillain"})
            assert response.status_code == status.HTTP_400_BAD_REQUEST


def describe_GuildViewSet():
    def describe_list():
        def it_requires_authentication(api_client):
            response = api_client.get("/api/v1/guilds/")
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

        def it_returns_active_guilds(admin_client):
            guild = GuildFactory(is_active=True)
            response = admin_client.get("/api/v1/guilds/")
            assert response.status_code == status.HTTP_200_OK
            ids = [r["id"] for r in response.data["results"]]
            assert guild.id in ids

        def it_excludes_inactive_guilds(admin_client):
            inactive = GuildFactory(is_active=False)
            response = admin_client.get("/api/v1/guilds/")
            ids = [r["id"] for r in response.data["results"]]
            assert inactive.id not in ids

    def describe_create_not_allowed():
        def it_blocks_post(admin_client):
            response = admin_client.post("/api/v1/guilds/", {"name": "New Guild"})
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


def describe_CommunityEventViewSet():
    def describe_list():
        def it_requires_authentication(api_client):
            response = api_client.get("/api/v1/events/")
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

        def it_returns_events_for_authenticated_users(member_client):
            CommunityEventFactory()
            response = member_client.get("/api/v1/events/")
            assert response.status_code == status.HTTP_200_OK

    def describe_create():
        def it_requires_fog_admin(member_client):
            response = member_client.post("/api/v1/events/", {})
            assert response.status_code == status.HTTP_403_FORBIDDEN

        def it_calls_schedule_or_go_live(admin_client, admin_member):
            payload = {
                "title": "Test Event",
                "event_type": "community",
                "starts_at": "2026-09-01T15:00:00Z",
                "ends_at": "2026-09-01T16:00:00Z",
                "location": "Tech Guild",
                "description": "A test event.",
                "recurrence": "none",
                "google_calendar_target": "member",
            }
            with patch.object(CommunityEvent, "schedule_or_go_live") as mock_sog:
                response = admin_client.post("/api/v1/events/", payload, format="json")
            assert response.status_code == status.HTTP_201_CREATED
            mock_sog.assert_called_once()

        def it_validates_guild_required_for_guild_meeting(admin_client):
            payload = {
                "title": "Guild Meeting",
                "event_type": "guild_meeting",
                "starts_at": "2026-09-01T15:00:00Z",
                "ends_at": "2026-09-01T16:00:00Z",
            }
            response = admin_client.post("/api/v1/events/", payload, format="json")
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "guild" in response.data

        def it_validates_ends_after_starts(admin_client):
            payload = {
                "title": "Bad Event",
                "event_type": "community",
                "starts_at": "2026-09-01T16:00:00Z",
                "ends_at": "2026-09-01T15:00:00Z",
            }
            response = admin_client.post("/api/v1/events/", payload, format="json")
            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def describe_update():
        def it_does_not_re_announce_on_update(admin_client):
            event = CommunityEventFactory()
            with patch.object(CommunityEvent, "schedule_or_go_live") as mock_sog:
                response = admin_client.patch(
                    f"/api/v1/events/{event.id}/",
                    {"title": "Updated Title"},
                    format="json",
                )
            assert response.status_code == status.HTTP_200_OK
            mock_sog.assert_not_called()


def describe_GuildAnnouncementViewSet():
    def describe_list():
        def it_requires_authentication(api_client):
            response = api_client.get("/api/v1/announcements/")
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

        def it_returns_announcements_for_authenticated_users(member_client):
            GuildAnnouncementFactory()
            response = member_client.get("/api/v1/announcements/")
            assert response.status_code == status.HTTP_200_OK

    def describe_create():
        def it_requires_fog_admin(member_client):
            response = member_client.post("/api/v1/announcements/", {})
            assert response.status_code == status.HTTP_403_FORBIDDEN

        def it_calls_notify_members(admin_client, admin_member):
            guild = GuildFactory()
            payload = {
                "guild": guild.id,
                "title": "Test Announcement",
                "body": "Hello members!",
                "send_email": True,
                "discord_channel": "none",
            }
            with patch.object(GuildAnnouncement, "notify_members") as mock_notify:
                response = admin_client.post("/api/v1/announcements/", payload, format="json")
            assert response.status_code == status.HTTP_201_CREATED
            mock_notify.assert_called_once()

        def it_sets_author_to_requesting_user(admin_client, admin_member):
            guild = GuildFactory()
            payload = {
                "guild": guild.id,
                "title": "Authored",
                "body": "Body text.",
                "send_email": False,
                "discord_channel": "none",
            }
            with patch.object(GuildAnnouncement, "notify_members"):
                response = admin_client.post("/api/v1/announcements/", payload, format="json")
            assert response.status_code == status.HTTP_201_CREATED
            announcement = GuildAnnouncement.objects.get(id=response.data["id"])
            assert announcement.author == admin_member.user


def describe_MembershipPlanViewSet():
    def describe_list():
        def it_requires_authentication(api_client):
            response = api_client.get("/api/v1/plans/")
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

        def it_requires_fog_admin(member_client):
            response = member_client.get("/api/v1/plans/")
            assert response.status_code == status.HTTP_403_FORBIDDEN

        def it_returns_all_plans(admin_client):
            plan = MembershipPlanFactory()
            response = admin_client.get("/api/v1/plans/")
            assert response.status_code == status.HTTP_200_OK
            ids = [r["id"] for r in response.data["results"]]
            assert plan.id in ids

    def describe_create():
        def it_creates_a_plan(admin_client):
            payload = {"name": "Gold Plan", "monthly_price": "75.00", "notes": ""}
            response = admin_client.post("/api/v1/plans/", payload, format="json")
            assert response.status_code == status.HTTP_201_CREATED
            assert response.data["name"] == "Gold Plan"
