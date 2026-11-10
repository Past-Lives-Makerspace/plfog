"""Site Settings, Discord tab: the leadership directory channel id (#1149)."""

from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client: Client) -> Client:
    User.objects.create_superuser(username="dir_admin", email="dir_admin@x.com", password="p")
    client.login(username="dir_admin", password="p")
    return client


def _post(**overrides: str) -> dict[str, str]:
    data = {
        "org_name": "Past Lives Makerspace",
        "registration_mode": "invite_only",
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        "feeds-TOTAL_FORMS": "0",
        "feeds-INITIAL_FORMS": "0",
        "emoji-TOTAL_FORMS": "0",
        "emoji-INITIAL_FORMS": "0",
        "guildroles-TOTAL_FORMS": "0",
        "guildroles-INITIAL_FORMS": "0",
        "submitted_tab": "discord",
    }
    data.update(overrides)
    return data


def describe_leadership_directory_channel_id():
    def it_renders_once_on_the_discord_tab(admin_client: Client):
        content = admin_client.get(reverse("hub_admin_site_settings") + "?tab=discord").content.decode()
        assert content.count('name="discord_leadership_directory_channel_id"') == 1
        assert "Leadership Directory Post" in content

    def it_never_shows_the_stored_message_ids_or_hash(admin_client: Client):
        content = admin_client.get(reverse("hub_admin_site_settings") + "?tab=discord").content.decode()
        assert 'name="discord_leadership_directory_message_ids"' not in content
        assert 'name="discord_leadership_directory_hash"' not in content

    def it_saves_digits_trimmed(admin_client: Client):
        admin_client.post(
            reverse("hub_admin_site_settings"), _post(discord_leadership_directory_channel_id=" 968637078738976868 ")
        )
        assert SiteConfiguration.load().discord_leadership_directory_channel_id == "968637078738976868"

    def it_refuses_anything_but_digits(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_admin_site_settings"), _post(discord_leadership_directory_channel_id="#leadership")
        )
        assert "A Discord channel id is numbers only" in resp.content.decode()
        assert SiteConfiguration.load().discord_leadership_directory_channel_id == ""

    def it_accepts_blank_to_turn_the_post_off(admin_client: Client):
        config = SiteConfiguration.load()
        config.discord_leadership_directory_channel_id = "968"
        config.save()
        admin_client.post(reverse("hub_admin_site_settings"), _post(discord_leadership_directory_channel_id=""))
        assert SiteConfiguration.load().discord_leadership_directory_channel_id == ""

    def it_never_writes_back_the_sync_state_a_sync_changed_meanwhile(admin_client: Client):
        from hub.forms import SiteSettingsForm

        config = SiteConfiguration.load()
        form = SiteSettingsForm(_post(org_name="Renamed Space"), instance=config)
        assert form.is_valid(), form.errors
        SiteConfiguration.objects.filter(pk=config.pk).update(
            discord_leadership_directory_message_ids=["501", "502"],
            discord_leadership_directory_hash="abc",
            discord_leadership_directory_updated_on=date(2026, 11, 9),
        )
        form.save()
        saved = SiteConfiguration.load()
        assert saved.org_name == "Renamed Space"
        assert saved.discord_leadership_directory_message_ids == ["501", "502"]
        assert saved.discord_leadership_directory_hash == "abc"
        assert saved.discord_leadership_directory_updated_on == date(2026, 11, 9)

    def it_writes_nothing_on_a_save_without_commit(admin_client: Client):
        from hub.forms import SiteSettingsForm

        form = SiteSettingsForm(_post(org_name="Not Yet"), instance=SiteConfiguration.load())
        assert form.is_valid(), form.errors
        assert form.save(commit=False).org_name == "Not Yet"
        assert SiteConfiguration.load().org_name != "Not Yet"
