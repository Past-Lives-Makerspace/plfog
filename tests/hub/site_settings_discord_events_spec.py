"""Site Settings — the "Publish events to Discord" toggle beside the Google one.

Rendered via ``form_field.html`` (→ the theme-correct ``toggle.html`` component, no inline
color), saved by the shared settings POST. Excluded from the General auto-loop so it renders
exactly once, on the Calendar tab next to the Google Calendar push toggle.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def _superuser(client: Client) -> None:
    from django.contrib.auth.models import User

    User.objects.create_superuser(username="dsadmin", email="dsadmin@x.com", password="p")
    client.login(username="dsadmin", password="p")


def _settings_post(**overrides: str) -> dict[str, str]:
    data = {
        "registration_mode": "invite_only",
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        "signage_default_slide_seconds": "12",
        "signage_event_days_ahead": "30",
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


def describe_discord_events_toggle_render():
    def it_renders_the_toggle_bound_to_the_field_exactly_once(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert 'name="discord_events_sync_enabled"' in content
        assert content.count('name="discord_events_sync_enabled"') == 1  # not doubled onto the General loop
        assert "Requires the bot's Manage Events permission" in content  # the hint copy
        assert "pl-toggle__slider" in content  # routed through the theme-correct toggle component


def describe_discord_calendar_posts_fields():
    def it_renders_the_channel_id_and_toggle_exactly_once(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert content.count('name="discord_calendar_channel_id"') == 1  # not doubled onto the General loop
        assert content.count('name="discord_calendar_posts_enabled"') == 1
        assert "The channel id of #calendar" in content  # the hint copy
        assert "Send Messages + Embed Links" in content

    def it_saves_the_channel_id_and_turns_the_toggle_on(client: Client):
        _superuser(client)
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(discord_calendar_channel_id="1309624926893768854", discord_calendar_posts_enabled="on"),
        )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.discord_calendar_channel_id == "1309624926893768854"
        assert config.discord_calendar_posts_enabled is True

    def it_turns_the_toggle_off_when_unchecked(client: Client):
        _superuser(client)
        config = SiteConfiguration.load()
        config.discord_calendar_posts_enabled = True
        config.save(update_fields=["discord_calendar_posts_enabled"])
        resp = client.post(reverse("hub_admin_site_settings"), _settings_post())  # checkbox absent → off
        assert resp.status_code == 302
        assert SiteConfiguration.load().discord_calendar_posts_enabled is False


def describe_discord_classes_posts_fields():
    def it_renders_the_channel_id_and_toggle_exactly_once(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert content.count('name="discord_classes_channel_id"') == 1  # not doubled onto the General loop
        assert content.count('name="discord_classes_posts_enabled"') == 1
        assert "The channel id of #classes" in content  # the hint copy

    def it_saves_the_channel_id_and_turns_the_toggle_on(client: Client):
        _superuser(client)
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(discord_classes_channel_id="946149249178021949", discord_classes_posts_enabled="on"),
        )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.discord_classes_channel_id == "946149249178021949"
        assert config.discord_classes_posts_enabled is True

    def it_turns_the_toggle_off_when_unchecked(client: Client):
        _superuser(client)
        config = SiteConfiguration.load()
        config.discord_classes_posts_enabled = True
        config.save(update_fields=["discord_classes_posts_enabled"])
        resp = client.post(reverse("hub_admin_site_settings"), _settings_post())  # checkbox absent → off
        assert resp.status_code == 302
        assert SiteConfiguration.load().discord_classes_posts_enabled is False


def describe_discord_events_toggle_save():
    def it_turns_the_toggle_on(client: Client):
        _superuser(client)
        resp = client.post(reverse("hub_admin_site_settings"), _settings_post(discord_events_sync_enabled="on"))
        assert resp.status_code == 302
        assert SiteConfiguration.load().discord_events_sync_enabled is True

    def it_turns_the_toggle_off_when_unchecked(client: Client):
        _superuser(client)
        config = SiteConfiguration.load()
        config.discord_events_sync_enabled = True
        config.save(update_fields=["discord_events_sync_enabled"])
        resp = client.post(reverse("hub_admin_site_settings"), _settings_post())  # checkbox absent → off
        assert resp.status_code == 302
        assert SiteConfiguration.load().discord_events_sync_enabled is False
