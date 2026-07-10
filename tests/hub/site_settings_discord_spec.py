"""Site Settings → Discord tab — the two makerspace-wide webhook fields.

Admins set #general-chat / #leadership webhooks here (via ``form_field.html``), saved by the
existing settings POST. The fields render only on the Discord tab (excluded from the General
loop) and live INSIDE ``#site-settings-form`` so the shared Save persists them (§6.2).
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
        # The site-settings page is one <form> spanning all tabs, so every save
        # carries member_event_policy (a required Calendar-tab field) — mirror that.
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        "feeds-TOTAL_FORMS": "0",
        "feeds-INITIAL_FORMS": "0",
        "feeds-MIN_NUM_FORMS": "0",
        "feeds-MAX_NUM_FORMS": "1000",
        "submitted_tab": "discord",
    }
    data.update(overrides)
    return data


def describe_discord_settings_save():
    def it_persists_both_webhook_fields(client: Client):
        _superuser(client)
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(discord_general_webhook_url=_HOOK, discord_leadership_webhook_url=_HOOK),
        )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.discord_general_webhook_url == _HOOK
        assert config.discord_leadership_webhook_url == _HOOK

    def it_rejects_a_malformed_webhook_url(client: Client):
        _superuser(client)
        resp = client.post(
            reverse("hub_admin_site_settings"),
            _settings_post(discord_general_webhook_url="not-a-url"),
        )
        assert resp.status_code == 200  # re-rendered with the field error, not saved
        assert SiteConfiguration.load().discord_general_webhook_url == ""


def describe_discord_tab_render():
    def it_shows_the_discord_tab_and_both_fields(client: Client):
        _superuser(client)
        resp = client.get(reverse("hub_admin_site_settings") + "?tab=discord")
        assert resp.status_code == 200
        content = resp.content.decode()
        assert 'name="discord_general_webhook_url"' in content
        assert 'name="discord_leadership_webhook_url"' in content
        assert "tab = 'discord'" in content  # the Discord tab button's @click
        assert "tab === 'discord'" in content  # the Discord tab section wrapper

    def it_renders_each_field_only_once_not_doubled_onto_general(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        # Excluded from the General loop → rendered exactly once (on the Discord tab).
        assert content.count('name="discord_general_webhook_url"') == 1
        assert content.count('name="discord_leadership_webhook_url"') == 1

    def it_places_the_fields_inside_the_main_settings_form(client: Client):
        _superuser(client)
        content = client.get(reverse("hub_admin_site_settings")).content.decode()
        # The field must appear before the first </form> — i.e. inside #site-settings-form,
        # not in the separate announcements composer that follows it.
        assert content.index('name="discord_general_webhook_url"') < content.index("</form>")
