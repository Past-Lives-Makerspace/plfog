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
