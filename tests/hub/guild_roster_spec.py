"""Join/leave a guild + gallery image delete."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from membership.models import GuildImage, GuildMembership, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    """Create a user (auto-linked to a Member via signal) with the given fog_role."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pw")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


@pytest.mark.django_db
def describe_join_leave():
    def it_lets_a_member_join_and_leave(client: Client):
        user = _member_user("m")
        client.login(username="m", password="pw")
        guild = GuildFactory()

        client.post(reverse("hub_guild_join", args=[guild.pk]))
        assert GuildMembership.objects.filter(guild=guild, member__user=user).exists()

        client.post(reverse("hub_guild_leave", args=[guild.pk]))
        assert not GuildMembership.objects.filter(guild=guild, member__user=user).exists()


@pytest.mark.django_db
def describe_image_delete():
    def it_deletes_an_image_for_an_editor(client: Client):
        _member_user("a", fog_role=Member.FogRole.ADMIN)
        client.login(username="a", password="pw")
        guild = GuildFactory()
        img = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("x.png", _PNG))
        client.post(reverse("hub_guild_image_delete", args=[guild.pk, img.pk]))
        assert not GuildImage.objects.filter(pk=img.pk).exists()
