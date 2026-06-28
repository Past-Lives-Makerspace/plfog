"""BDD specs for guild social/contact buttons and the welcome-on-join email."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse

from hub.forms import GuildEditForm
from membership.models import GuildMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def describe_guild_join_welcome_email():
    def it_emails_a_new_member_on_join(client: Client):
        _user_with_role("gj1")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(
            guild=guild,
            is_enabled=True,
            join_email_enabled=True,
            join_email_subject="Welcome aboard!",
            join_email_body="So glad you're here.",
        )
        client.login(username="gj1", password="pass")
        client.post(reverse("hub_guild_join", args=[guild.pk]))
        assert any(m.subject == "Welcome aboard!" for m in mail.outbox)

    def it_does_not_email_on_rejoin(client: Client):
        user = _user_with_role("gj2")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(
            guild=guild,
            is_enabled=True,
            join_email_enabled=True,
            join_email_subject="Welcome aboard!",
            join_email_body="Hi",
        )
        GuildMembership.objects.create(guild=guild, member=user.member)
        mail.outbox.clear()
        client.login(username="gj2", password="pass")
        client.post(reverse("hub_guild_join", args=[guild.pk]))
        assert mail.outbox == []


def describe_guild_social_buttons():
    def it_shows_email_lead_discord_and_website(client: Client):
        _user_with_role("gs1")
        lead = MemberFactory(full_legal_name="Lead Person")
        guild = GuildFactory(
            guild_lead=lead, discord_url="https://discord.gg/example", website_url="https://example.com"
        )
        client.login(username="gs1", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"Email Guild Lead" in response.content
        assert b"Discord channel" in response.content
        assert b"Website" in response.content


def describe_GuildEditForm_social_urls():
    def it_accepts_discord_and_website_urls():
        form = GuildEditForm(
            data={
                "name": "Social Guild",
                "calendar_color": "#4B9FEE",
                "discord_url": "https://discord.gg/example",
                "website_url": "https://example.com",
            }
        )
        assert form.is_valid(), form.errors
