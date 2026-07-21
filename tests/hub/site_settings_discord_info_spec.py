"""Site Settings → Discord — the FOG-managed #important-info pinned post fields.

Rendered via ``form_field.html`` on the Discord tab (excluded from the General auto-loop),
saved by the shared settings POST. A save that touches any info-post field re-syncs the
pinned Discord message (mocked here at the service boundary); a Discord failure surfaces
as an admin error message while the local content still saves.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from core.integrations.discord_channel import DiscordChannelError
from core.models import DISCORD_INFO_LINKS_DEFAULT, SiteConfiguration

pytestmark = pytest.mark.django_db

_CHANNEL_ID = "1122351596400025661"
_MESSAGE_ID = "1529134555494355147"


def _superuser(client: Client) -> None:
    from django.contrib.auth.models import User

    User.objects.create_superuser(username="diadmin", email="diadmin@x.com", password="p")
    client.login(username="diadmin", password="p")


def _settings_post(**overrides: str) -> dict[str, str]:
    data = {
        "registration_mode": "invite_only",
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        "signage_default_slide_seconds": "12",
        "signage_event_days_ahead": "30",
        "discord_info_links_content": DISCORD_INFO_LINKS_DEFAULT,
        "feeds-TOTAL_FORMS": "0",
        "feeds-INITIAL_FORMS": "0",
        "feeds-MIN_NUM_FORMS": "0",
        "feeds-MAX_NUM_FORMS": "1000",
        "emoji-TOTAL_FORMS": "0",
        "emoji-INITIAL_FORMS": "0",
        "emoji-MIN_NUM_FORMS": "0",
        "emoji-MAX_NUM_FORMS": "1000",
        "guildroles-TOTAL_FORMS": "0",
        "guildroles-INITIAL_FORMS": "0",
        "guildroles-MIN_NUM_FORMS": "0",
        "guildroles-MAX_NUM_FORMS": "1000",
        "submitted_tab": "general",
    }
    data.update(overrides)
    return data


def describe_info_post_fields_render():
    def it_renders_each_field_exactly_once_with_the_sync_hint(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert content.count('name="discord_info_channel_id"') == 1  # not doubled onto the General loop
        assert content.count('name="discord_info_message_id"') == 1
        assert content.count('name="discord_info_links_content"') == 1
        assert "Saving updates the pinned Discord post immediately" in content
        assert "#important-info pinned post" in content


def describe_info_post_save():
    def it_saves_the_ids_and_content(client: Client):
        _superuser(client)
        with patch("hub.discord_info_post.sync_info_post"):
            resp = client.post(
                reverse("hub_admin_site_settings"),
                _settings_post(
                    discord_info_channel_id=_CHANNEL_ID,
                    discord_info_message_id=_MESSAGE_ID,
                    discord_info_links_content="**Wiki**\nhttps://wiki.pastlives.space",
                ),
            )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.discord_info_channel_id == _CHANNEL_ID
        assert config.discord_info_message_id == _MESSAGE_ID
        assert config.discord_info_links_content == "**Wiki**\nhttps://wiki.pastlives.space"

    def it_rejects_content_over_discords_embed_limit(client: Client):
        _superuser(client)
        with patch("hub.discord_info_post.sync_info_post") as fake_sync:
            resp = client.post(
                reverse("hub_admin_site_settings"),
                _settings_post(discord_info_links_content="x" * 4097),
            )
        assert resp.status_code == 200  # re-rendered with the validation error, not saved
        assert "Keep the links content under" in resp.content.decode()  # apostrophes HTML-escape; assert plain part
        assert SiteConfiguration.load().discord_info_links_content == DISCORD_INFO_LINKS_DEFAULT
        fake_sync.assert_not_called()


def describe_info_post_sync_on_save():
    def it_syncs_when_an_info_field_changed(client: Client):
        _superuser(client)
        with patch("hub.discord_info_post.sync_info_post") as fake_sync:
            resp = client.post(
                reverse("hub_admin_site_settings"),
                _settings_post(discord_info_links_content="**New links**"),
            )
        assert resp.status_code == 302
        fake_sync.assert_called_once_with()

    def it_does_not_call_discord_when_no_info_field_changed(client: Client):
        _superuser(client)
        with patch("hub.discord_info_post.sync_info_post") as fake_sync:
            resp = client.post(reverse("hub_admin_site_settings"), _settings_post())
        assert resp.status_code == 302
        fake_sync.assert_not_called()

    def it_surfaces_a_discord_failure_and_still_saves_the_content(client: Client):
        _superuser(client)
        with patch("hub.discord_info_post.sync_info_post", side_effect=DiscordChannelError("Discord API 403: nope")):
            resp = client.post(
                reverse("hub_admin_site_settings"),
                _settings_post(discord_info_links_content="**New links**"),
                follow=True,
            )
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "updating the pinned #important-info Discord post failed" in content
        assert SiteConfiguration.load().discord_info_links_content == "**New links**"
