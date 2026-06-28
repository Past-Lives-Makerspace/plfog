"""BDD specs for CommunityEvent CRUD: the form, the lead views (gated, guild-scoped,
cross-guild isolation), and the admin authoring endpoints + the Events tab."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from hub.forms import CommunityEventForm
from membership.models import CommunityEvent, GuildStaffMembership, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _event_payload(**overrides: str) -> dict:
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
def describe_form():
    def it_omits_type_and_guild_for_the_lead_variant():
        form = CommunityEventForm(as_admin=False)
        assert "event_type" not in form.fields
        assert "guild" not in form.fields
        assert "recurrence" in form.fields

    def it_errors_when_end_is_not_after_start():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", ends_at="2026-07-11T18:00", starts_at="2026-07-11T20:00"),
            as_admin=True,
        )
        assert not form.is_valid()
        assert "End time must be after the start." in form.errors["ends_at"]

    def it_errors_for_a_guild_meeting_without_a_guild():
        form = CommunityEventForm(data=_event_payload(event_type="guild_meeting"), as_admin=True)
        assert not form.is_valid()
        assert "guild" in form.errors

    def it_errors_for_a_site_wide_type_with_a_guild():
        guild = GuildFactory()
        form = CommunityEventForm(data=_event_payload(event_type="community", guild=str(guild.pk)), as_admin=True)
        assert not form.is_valid()
        assert "guild" in form.errors

    def it_saves_a_valid_admin_guild_meeting():
        guild = GuildFactory()
        form = CommunityEventForm(data=_event_payload(event_type="guild_meeting", guild=str(guild.pk)), as_admin=True)
        assert form.is_valid(), form.errors


@pytest.mark.django_db
def describe_lead_gating():
    def it_403s_a_non_staff_member_on_the_list(client: Client):
        _user_with_role("m1")
        guild = GuildFactory()
        client.login(username="m1", password="pass")
        assert client.get(reverse("hub_guild_events", args=[guild.pk])).status_code == 403

    def it_403s_a_non_staff_member_on_add(client: Client):
        _user_with_role("m2")
        guild = GuildFactory()
        client.login(username="m2", password="pass")
        assert client.get(reverse("hub_guild_event_add", args=[guild.pk])).status_code == 403

    def it_403s_a_non_staff_member_on_delete(client: Client):
        _user_with_role("m3")
        guild = GuildFactory()
        event = CommunityEventFactory(guild=guild)
        client.login(username="m3", password="pass")
        assert client.post(reverse("hub_guild_event_delete", args=[guild.pk, event.pk])).status_code == 403

    def it_lets_the_guild_lead_open_the_list(client: Client):
        user = _user_with_role("lead1")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead1", password="pass")
        assert client.get(reverse("hub_guild_events", args=[guild.pk])).status_code == 200

    def it_lets_a_staff_member_open_the_list(client: Client):
        user = _user_with_role("staff1")
        guild = GuildFactory()
        GuildStaffMembership.objects.create(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
        client.login(username="staff1", password="pass")
        assert client.get(reverse("hub_guild_events", args=[guild.pk])).status_code == 200


@pytest.mark.django_db
def describe_lead_create_and_edit():
    def it_creates_a_guild_meeting_and_announces_once(client: Client):
        user = _user_with_role("c1")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="c1", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(reverse("hub_guild_event_add", args=[guild.pk]), data=_event_payload())
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(guild=guild)
        assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert event.created_by == user
        mock_announce.assert_called_once()

    def it_does_not_re_announce_on_edit(client: Client):
        user = _user_with_role("c2")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild=guild, title="Old")
        client.login(username="c2", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(
                reverse("hub_guild_event_edit", args=[guild.pk, event.pk]), data=_event_payload(title="New")
            )
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.title == "New"
        mock_announce.assert_not_called()


@pytest.mark.django_db
def describe_cross_guild_isolation():
    def it_404s_a_lead_editing_another_guilds_event(client: Client):
        user = _user_with_role("iso1")
        guild_a = GuildFactory(guild_lead=user.member)
        guild_b = GuildFactory()
        b_event = CommunityEventFactory(guild=guild_b, title="B's event")
        client.login(username="iso1", password="pass")
        resp = client.post(
            reverse("hub_guild_event_edit", args=[guild_a.pk, b_event.pk]), data=_event_payload(title="Hijacked")
        )
        assert resp.status_code == 404
        b_event.refresh_from_db()
        assert b_event.title == "B's event"

    def it_404s_a_lead_deleting_another_guilds_event(client: Client):
        user = _user_with_role("iso2")
        guild_a = GuildFactory(guild_lead=user.member)
        guild_b = GuildFactory()
        b_event = CommunityEventFactory(guild=guild_b)
        client.login(username="iso2", password="pass")
        resp = client.post(reverse("hub_guild_event_delete", args=[guild_a.pk, b_event.pk]))
        assert resp.status_code == 404
        assert CommunityEvent.objects.filter(pk=b_event.pk).exists()


@pytest.mark.django_db
def describe_admin_authoring():
    def it_403s_a_non_admin_on_add(client: Client):
        _user_with_role("na1")
        client.login(username="na1", password="pass")
        assert client.get(reverse("hub_event_add")).status_code == 403

    def it_403s_a_non_admin_on_delete(client: Client):
        _user_with_role("na2")
        event = CommunityEventFactory(community=True)
        client.login(username="na2", password="pass")
        assert client.post(reverse("hub_event_delete", args=[event.pk])).status_code == 403

    def it_lets_an_admin_open_the_add_page(client: Client):
        _user_with_role("ad1", fog_role=Member.FogRole.ADMIN)
        client.login(username="ad1", password="pass")
        assert client.get(reverse("hub_event_add")).status_code == 200

    def it_lets_an_admin_create_a_community_event_and_announces(client: Client):
        _user_with_role("ad2", fog_role=Member.FogRole.ADMIN)
        client.login(username="ad2", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(
                reverse("hub_event_add"),
                data=_event_payload(event_type="community", guild="", title="Potluck"),
            )
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Potluck")
        assert event.is_site_wide
        mock_announce.assert_called_once()

    def it_deletes_an_event_as_admin(client: Client):
        _user_with_role("ad3", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)
        client.login(username="ad3", password="pass")
        resp = client.post(reverse("hub_event_delete", args=[event.pk]))
        assert resp.status_code == 302
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()

    def it_rejects_a_get_on_delete(client: Client):
        _user_with_role("ad4", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)
        client.login(username="ad4", password="pass")
        assert client.get(reverse("hub_event_delete", args=[event.pk])).status_code == 405


@pytest.mark.django_db
def describe_events_tab_visibility():
    def it_shows_the_list_read_only_to_a_plain_member(client: Client):
        _user_with_role("v1")
        CommunityEventFactory(community=True, title="Open Mic Night")
        client.login(username="v1", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert resp.status_code == 200
        assert b"Open Mic Night" in resp.content
        assert b"+ Add event" not in resp.content
        assert reverse("hub_event_add").encode() not in resp.content

    def it_shows_management_controls_to_an_admin(client: Client):
        _user_with_role("v2", fog_role=Member.FogRole.ADMIN)
        CommunityEventFactory(community=True, title="Open Mic Night")
        client.login(username="v2", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert b"+ Add event" in resp.content
        assert reverse("hub_event_add").encode() in resp.content
