"""BDD specs for the guild QR-code download (editor-gated, SVG default + PNG)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def describe_guild_qr_download():
    def it_serves_an_svg_attachment_to_a_lead(client: Client):
        user = _user_with_role("lead_svg")
        guild = GuildFactory(guild_lead=user.member, name="QR Guild")
        client.login(username="lead_svg", password="pass")
        resp = client.get(reverse("hub_guild_qr", args=[guild.pk, "svg"]))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/svg+xml"
        assert resp["Content-Disposition"] == f'attachment; filename="{guild.slug}-qr.svg"'
        assert b"<svg" in resp.content

    def it_serves_a_png_attachment_to_a_lead(client: Client):
        user = _user_with_role("lead_png")
        guild = GuildFactory(guild_lead=user.member, name="QR Guild")
        client.login(username="lead_png", password="pass")
        resp = client.get(reverse("hub_guild_qr", args=[guild.pk, "png"]))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"
        assert resp["Content-Disposition"] == f'attachment; filename="{guild.slug}-qr.png"'
        assert resp.content.startswith(b"\x89PNG")

    def it_admin_can_download(client: Client):
        _user_with_role("admin_qr", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="QR Guild")
        client.login(username="admin_qr", password="pass")
        assert client.get(reverse("hub_guild_qr", args=[guild.pk, "svg"])).status_code == 200

    def it_forbids_a_non_editor_member(client: Client):
        _user_with_role("reg_qr")
        guild = GuildFactory(name="QR Guild")
        client.login(username="reg_qr", password="pass")
        assert client.get(reverse("hub_guild_qr", args=[guild.pk, "svg"])).status_code == 403

    def it_redirects_anonymous_to_login(client: Client):
        guild = GuildFactory(name="QR Guild")
        resp = client.get(reverse("hub_guild_qr", args=[guild.pk, "png"]))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def it_404s_for_an_unknown_format(client: Client):
        user = _user_with_role("lead_bad")
        guild = GuildFactory(guild_lead=user.member, name="QR Guild")
        client.login(username="lead_bad", password="pass")
        assert client.get(reverse("hub_guild_qr", args=[guild.pk, "gif"])).status_code == 404
