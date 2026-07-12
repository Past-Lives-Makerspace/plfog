"""Site Settings → Discord tab — the makerspace-wide webhook fields.

Admins set #general-chat / #leadership / #guild-officers webhooks here (via ``form_field.html``),
saved by the existing settings POST. The fields render only on the Discord tab (excluded from the
General loop) and live INSIDE ``#site-settings-form`` so the shared Save persists them (§6.2).
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

_HOOK = "https://discord.com/api/webhooks/7/general"


def _superuser(client: Client) -> None:
    from django.contrib.auth.models import User

    User.objects.create_superuser(username="ssadmin", email="ssadmin@x.com", password="p")
    client.login(username="ssadmin", password="p")


def _settings_post(**overrides: str) -> dict[str, str]:
    data = {
        "registration_mode": "invite_only",
        "discord_general_webhook_url": "",
        "discord_leadership_webhook_url": "",
        "discord_officers_webhook_url": "",
        # The site-settings page is one <form> spanning all tabs, so every save
        # carries member_event_policy (a required Calendar-tab field) — mirror that.
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        # Signage global fields are required PositiveIntegerFields on the shared form.
        "signage_default_slide_seconds": "12",
        "signage_event_days_ahead": "30",
        "feeds-TOTAL_FORMS": "0",
        "feeds-INITIAL_FORMS": "0",
        "feeds-MIN_NUM_FORMS": "0",
        "feeds-MAX_NUM_FORMS": "1000",
        # The Discord tab now also carries the emoji-map + guild-role formsets in the same
        # <form>, so a discord save must include their (empty) management forms.
        "emoji-TOTAL_FORMS": "0",
        "emoji-INITIAL_FORMS": "0",
        "emoji-MIN_NUM_FORMS": "0",
        "emoji-MAX_NUM_FORMS": "1000",
        "guildroles-TOTAL_FORMS": "0",
        "guildroles-INITIAL_FORMS": "0",
        "guildroles-MIN_NUM_FORMS": "0",
        "guildroles-MAX_NUM_FORMS": "1000",
        "submitted_tab": "discord",
    }
    data.update(overrides)
    return data


def describe_discord_settings_save():
    def it_persists_all_webhook_fields(client: Client):
        _superuser(client)
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(
                discord_general_webhook_url=_HOOK,
                discord_leadership_webhook_url=_HOOK,
                discord_officers_webhook_url=_HOOK,
            ),
        )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.discord_general_webhook_url == _HOOK
        assert config.discord_leadership_webhook_url == _HOOK
        assert config.discord_officers_webhook_url == _HOOK

    def it_rejects_a_malformed_webhook_url(client: Client):
        _superuser(client)
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(discord_general_webhook_url="not-a-url"),
        )
        assert resp.status_code == 200  # re-rendered with the field error, not saved
        assert SiteConfiguration.load().discord_general_webhook_url == ""


def describe_discord_tab_render():
    def it_shows_the_discord_tab_and_all_fields(client: Client):
        _superuser(client)
        resp = client.get(reverse("hub_admin_site_settings") + "?tab=discord")
        assert resp.status_code == 200
        content = resp.content.decode()
        assert 'name="discord_general_webhook_url"' in content
        assert 'name="discord_leadership_webhook_url"' in content
        assert 'name="discord_officers_webhook_url"' in content
        assert "tab = 'discord'" in content  # the Discord tab button's @click
        assert "tab === 'discord'" in content  # the Discord tab section wrapper

    def it_shows_the_connection_ids_emoji_map_and_role_table(client: Client):
        from tests.membership.factories import GuildFactory

        _superuser(client)
        GuildFactory(name="Woodshop")
        resp = client.get(reverse("hub_admin_site_settings") + "?tab=discord")
        content = resp.content.decode()
        assert 'name="discord_server_id"' in content
        assert 'name="discord_role_message_id"' in content
        assert "Emoji → guild map" in content
        assert "No emoji mappings yet" in content  # empty state
        assert "Guild → Discord role" in content
        assert "Woodshop" in content  # the per-guild role row


def describe_discord_connection_ids():
    def it_persists_the_server_channel_and_message_ids(client: Client):
        _superuser(client)
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(
                discord_server_id="933",
                discord_role_message_channel_id="1121",
                discord_role_message_id="1301",
            ),
        )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.discord_server_id == "933"
        assert config.discord_role_message_channel_id == "1121"
        assert config.discord_role_message_id == "1301"


def describe_emoji_map_editor():
    def it_adds_an_emoji_mapping_row(client: Client):
        from membership.models import DiscordGuildEmoji
        from tests.membership.factories import GuildFactory

        _superuser(client)
        guild = GuildFactory()
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(
                **{
                    "emoji-TOTAL_FORMS": "1",
                    "emoji-INITIAL_FORMS": "0",
                    "emoji-0-emoji": "🔥",
                    "emoji-0-guild": str(guild.pk),
                    "emoji-0-id": "",
                }
            ),
        )
        assert resp.status_code == 302
        assert DiscordGuildEmoji.objects.filter(emoji="🔥", guild=guild).exists()

    def it_rejects_a_duplicate_emoji(client: Client):
        from membership.models import DiscordGuildEmoji
        from tests.membership.factories import DiscordGuildEmojiFactory, GuildFactory

        _superuser(client)
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(
                **{
                    "emoji-TOTAL_FORMS": "1",
                    "emoji-INITIAL_FORMS": "0",
                    "emoji-0-emoji": "🔥",  # duplicate
                    "emoji-0-guild": str(guild.pk),
                    "emoji-0-id": "",
                }
            ),
        )
        assert resp.status_code == 200  # re-rendered with the error, not saved
        assert DiscordGuildEmoji.objects.filter(emoji="🔥").count() == 1

    def it_deletes_a_saved_mapping_row(client: Client):
        from membership.models import DiscordGuildEmoji
        from tests.membership.factories import DiscordGuildEmojiFactory, GuildFactory

        _superuser(client)
        guild = GuildFactory()
        row = DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(
                **{
                    "emoji-TOTAL_FORMS": "1",
                    "emoji-INITIAL_FORMS": "1",
                    "emoji-0-emoji": "🔥",
                    "emoji-0-guild": str(guild.pk),
                    "emoji-0-id": str(row.pk),
                    "emoji-0-DELETE": "on",
                }
            ),
        )
        assert resp.status_code == 302
        assert not DiscordGuildEmoji.objects.filter(pk=row.pk).exists()


def describe_guild_role_editor():
    def it_saves_multiple_role_ids_in_lockstep(client: Client):
        from tests.membership.factories import GuildFactory

        _superuser(client)
        guild = GuildFactory(name="Glass")
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(
                **{
                    "guildroles-TOTAL_FORMS": "1",
                    "guildroles-INITIAL_FORMS": "1",
                    "guildroles-0-id": str(guild.pk),
                    "guildroles-0-discord_role_ids": "111, 222",
                }
            ),
        )
        assert resp.status_code == 302
        guild.refresh_from_db()
        assert guild.discord_role_ids == ["111", "222"]

    def it_rejects_a_non_numeric_role_id(client: Client):
        from tests.membership.factories import GuildFactory

        _superuser(client)
        guild = GuildFactory()
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(
                **{
                    "guildroles-TOTAL_FORMS": "1",
                    "guildroles-INITIAL_FORMS": "1",
                    "guildroles-0-id": str(guild.pk),
                    "guildroles-0-discord_role_ids": "not-a-number",
                }
            ),
        )
        assert resp.status_code == 200  # re-rendered with the error
        guild.refresh_from_db()
        assert guild.discord_role_ids == []

    def it_renders_each_field_only_once_not_doubled_onto_general(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        # Excluded from the General loop → rendered exactly once (on the Discord tab).
        assert content.count('name="discord_general_webhook_url"') == 1
        assert content.count('name="discord_leadership_webhook_url"') == 1
        assert content.count('name="discord_officers_webhook_url"') == 1

    def it_places_the_fields_inside_the_main_settings_form(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        # The field must appear before the first </form> — i.e. inside #site-settings-form,
        # not in the separate announcements composer that follows it.
        assert content.index('name="discord_general_webhook_url"') < content.index("</form>")
