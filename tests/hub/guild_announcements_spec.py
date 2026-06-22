"""Deleting guild announcements.

NOTE: posting/publishing announcements (which fires the ``guild_announcement``
notification via ``GuildAnnouncement.publish``) is deferred until Plan 2's
``core.notifications`` lands — see DEFERRED.md. Only the delete endpoint, which
has no notification dependency, is covered here for now.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import GuildAnnouncement, Member
from tests.membership.factories import GuildAnnouncementFactory, GuildFactory, MembershipPlanFactory


def _editor_user(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pw")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


@pytest.mark.django_db
def describe_announcement_delete():
    def it_deletes_an_announcement_for_an_editor(client: Client):
        _editor_user("a")
        client.login(username="a", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild)
        client.post(reverse("hub_guild_announcement_delete", args=[guild.pk, announcement.pk]))
        assert not GuildAnnouncement.objects.filter(pk=announcement.pk).exists()


@pytest.mark.django_db
def describe_announcement_delete_permissions():
    def it_forbids_non_editors(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="plain_ann", password="pw")
        member = user.member
        member.fog_role = Member.FogRole.MEMBER
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
        client.login(username="plain_ann", password="pw")
        guild = GuildFactory()
        announcement = GuildAnnouncementFactory(guild=guild)
        resp = client.post(reverse("hub_guild_announcement_delete", args=[guild.pk, announcement.pk]))
        assert resp.status_code == 403
        assert GuildAnnouncement.objects.filter(pk=announcement.pk).exists()


@pytest.mark.django_db
def describe_announcement_create():
    def it_creates_an_announcement_for_an_editor(client: Client):
        _editor_user("ac")
        client.login(username="ac", password="pw")
        guild = GuildFactory()
        resp = client.post(
            reverse("hub_guild_announcement_create", args=[guild.pk]),
            {"title": "Forge night!", "body": "This Friday.", "expires_at": ""},
        )
        assert resp.status_code == 302
        announcement = GuildAnnouncement.objects.get(guild=guild, title="Forge night!")
        assert announcement.body == "This Friday."
        assert announcement.author is not None

    def it_rejects_an_empty_announcement(client: Client):
        _editor_user("ac_empty")
        client.login(username="ac_empty", password="pw")
        guild = GuildFactory()
        resp = client.post(reverse("hub_guild_announcement_create", args=[guild.pk]), {"title": "", "body": ""})
        assert resp.status_code == 302
        assert not GuildAnnouncement.objects.filter(guild=guild).exists()

    def it_forbids_non_editors(client: Client):
        MembershipPlanFactory()
        User.objects.create_user(username="plain_ac", password="pw")  # default fog_role MEMBER
        client.login(username="plain_ac", password="pw")
        guild = GuildFactory()
        resp = client.post(reverse("hub_guild_announcement_create", args=[guild.pk]), {"title": "x", "body": "y"})
        assert resp.status_code == 403
        assert not GuildAnnouncement.objects.filter(guild=guild).exists()


@pytest.mark.django_db
def describe_announcement_display():
    def it_shows_active_announcements_on_the_guild_page(client: Client):
        _editor_user("disp")
        client.login(username="disp", password="pw")
        guild = GuildFactory()
        GuildAnnouncementFactory(guild=guild, title="LiveAnnounce")
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"LiveAnnounce" in resp.content

    def it_hides_expired_announcements_on_the_guild_page(client: Client):
        from datetime import timedelta

        from django.utils import timezone

        _editor_user("disp2")
        client.login(username="disp2", password="pw")
        guild = GuildFactory()
        GuildAnnouncementFactory(guild=guild, title="GoneAnnounce", expires_at=timezone.localdate() - timedelta(days=1))
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"GoneAnnounce" not in resp.content
