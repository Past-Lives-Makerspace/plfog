"""The guild-settings Discord card: webhook field + "also post" toggle.

Covers the form round-trip, the secret-leak guard (contrasting labels + webhook-shape
validation so a public invite link can't be saved as the private webhook), and the
Meetings-tab card rendering both controls with the toggle defaulting ON.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from hub.forms import GuildEditForm
from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_VALID_WEBHOOK = "https://discord.com/api/webhooks/123/abcdef"


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.ADMIN) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def describe_GuildEditForm_discord_fields():
    def it_round_trips_both_new_fields():
        guild = GuildFactory()
        form = GuildEditForm(
            data={"name": guild.name, "discord_webhook_url": _VALID_WEBHOOK, "discord_post_enabled": "on"},
            instance=guild,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.discord_webhook_url == _VALID_WEBHOOK
        assert saved.discord_post_enabled is True

    def it_accepts_a_valid_discord_webhook():
        guild = GuildFactory()
        form = GuildEditForm(data={"name": guild.name, "discord_webhook_url": _VALID_WEBHOOK}, instance=guild)
        assert form.is_valid(), form.errors

    def it_accepts_a_blank_webhook():
        guild = GuildFactory()
        form = GuildEditForm(data={"name": guild.name, "discord_webhook_url": ""}, instance=guild)
        assert form.is_valid(), form.errors

    def it_rejects_a_non_discord_webhook_url():
        # A mis-pasted public invite link (or any non-Discord URL) must not save as the
        # private webhook — it would either publish the secret or fail silently later.
        guild = GuildFactory()
        form = GuildEditForm(
            data={"name": guild.name, "discord_webhook_url": "https://discord.gg/some-invite"},
            instance=guild,
        )
        assert not form.is_valid()
        assert "discord_webhook_url" in form.errors

    def it_labels_the_public_link_and_the_private_webhook_distinctly():
        # Secret-leak guard: the two fields share the word "Discord" but mean different
        # things — the labels must keep them unmistakable.
        form = GuildEditForm()
        assert form.fields["discord_url"].label == "Discord channel link (shown to members)"
        assert form.fields["discord_webhook_url"].label == "Announcement webhook (auto-posts here — keep private)"

    def it_defaults_the_post_toggle_on():
        guild = GuildFactory()  # model default discord_post_enabled=True
        form = GuildEditForm(instance=guild)
        assert form["discord_post_enabled"].value() is True


def describe_guild_edit_discord_card():
    def it_renders_both_controls_on_the_meetings_tab(client: Client):
        _user_with_role("disc_admin")
        guild = GuildFactory()
        client.login(username="disc_admin", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert ">Discord</h2>" in content
        assert 'name="discord_webhook_url"' in content
        assert "https://discord.com/api/webhooks/..." in content  # the webhook input placeholder
        assert 'name="discord_post_enabled"' in content
        assert "pl-toggle" in content  # the boolean renders as a toggle, never a raw checkbox
        assert "Also post to our Discord" in content
        assert "Announcement webhook (auto-posts here — keep private)" in content

    def it_persists_the_fields_through_a_full_page_save(client: Client):
        user = _user_with_role("disc_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="disc_lead", password="pass")
        response = client.post(
            reverse("hub_guild_edit", args=[guild.pk]),
            data={
                "name": guild.name,
                "about": "",
                "discord_webhook_url": _VALID_WEBHOOK,
                "discord_post_enabled": "on",
            },
        )
        assert response.status_code == 302
        guild.refresh_from_db()
        assert guild.discord_webhook_url == _VALID_WEBHOOK
        assert guild.discord_post_enabled is True
