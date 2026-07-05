"""BDD specs for the printable guild flyer (standalone print page, editor-gated)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

FLYER_SETTINGS = dict(
    MEMBER_BASE_URL="https://pastlives.app",
    GUILDS_BASE_URL="https://guilds.pastlives.app",
)


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def describe_guild_flyer():
    @pytest.fixture(autouse=True)
    def _settings():
        with override_settings(**FLYER_SETTINGS):
            yield

    def it_renders_the_flyer_for_a_lead(client: Client):
        user = _user_with_role("lead_fly")
        guild = GuildFactory(guild_lead=user.member, name="Flyer Guild")
        client.login(username="lead_fly", password="pass")
        resp = client.get(reverse("hub_guild_flyer", args=[guild.pk]))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Flyer Guild" in body  # the guild name
        assert f"https://pastlives.app/g/{guild.slug}/" in body  # the vanity URL text
        assert "<svg" in body  # inline QR

    def it_is_a_standalone_page_without_member_chrome(client: Client):
        user = _user_with_role("lead_chrome")
        guild = GuildFactory(guild_lead=user.member, name="Flyer Guild")
        client.login(username="lead_chrome", password="pass")
        body = client.get(reverse("hub_guild_flyer", args=[guild.pk])).content
        assert b"hub-sidebar" not in body
        assert b"pl-topbar" not in body

    def it_shows_essential_rules_when_set(client: Client):
        user = _user_with_role("lead_rules")
        guild = GuildFactory(
            guild_lead=user.member,
            name="Flyer Guild",
            essential_rules="Closed-toe shoes in the shop.",
        )
        client.login(username="lead_rules", password="pass")
        body = client.get(reverse("hub_guild_flyer", args=[guild.pk])).content.decode()
        assert "Closed-toe shoes in the shop." in body

    def it_omits_the_rules_section_when_blank(client: Client):
        user = _user_with_role("lead_norules")
        guild = GuildFactory(guild_lead=user.member, name="Flyer Guild")
        client.login(username="lead_norules", password="pass")
        body = client.get(reverse("hub_guild_flyer", args=[guild.pk])).content.decode()
        assert "Essential rules" not in body

    def it_admin_can_open_the_flyer(client: Client):
        _user_with_role("admin_fly", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Flyer Guild")
        client.login(username="admin_fly", password="pass")
        assert client.get(reverse("hub_guild_flyer", args=[guild.pk])).status_code == 200

    def it_forbids_a_non_editor_member(client: Client):
        _user_with_role("reg_fly")
        guild = GuildFactory(name="Flyer Guild")
        client.login(username="reg_fly", password="pass")
        assert client.get(reverse("hub_guild_flyer", args=[guild.pk])).status_code == 403

    def it_redirects_anonymous_to_login(client: Client):
        guild = GuildFactory(name="Flyer Guild")
        resp = client.get(reverse("hub_guild_flyer", args=[guild.pk]))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]
