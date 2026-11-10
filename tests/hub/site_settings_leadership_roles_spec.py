"""Site Settings, Discord tab: the leadership, Guild Officers and per guild lead role ids (#1148)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client: Client) -> Client:
    User.objects.create_superuser(username="roles_admin", email="roles_admin@x.com", password="p")
    client.login(username="roles_admin", password="p")
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


def describe_site_role_ids():
    def it_renders_each_on_the_discord_tab_once(admin_client: Client):
        GuildFactory(name="Woodshop")
        content = admin_client.get(reverse("hub_admin_site_settings") + "?tab=discord").content.decode()
        assert content.count('name="discord_leadership_role_id"') == 1
        assert content.count('name="discord_guild_officers_role_id"') == 1
        assert 'name="guildroles-0-discord_lead_role_id"' in content
        assert "Woodshop lead role id" in content

    def it_saves_both_ids(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(discord_leadership_role_id=" 1534 ", discord_guild_officers_role_id="1535"),
        )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.discord_leadership_role_id == "1534"
        assert config.discord_guild_officers_role_id == "1535"

    @pytest.mark.parametrize("value", ["officers", "\u00b9\u00b2"])
    def it_refuses_an_id_that_is_not_a_number(admin_client: Client, value: str):
        resp = admin_client.post(reverse("hub_admin_site_settings"), _post(discord_guild_officers_role_id=value))
        assert resp.status_code == 200
        assert "A Discord role id is numbers only" in resp.content.decode()
        assert SiteConfiguration.load().discord_guild_officers_role_id == ""


def describe_role_id_clashes():
    def it_refuses_leadership_equal_to_the_member_role_and_saves_nothing(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(discord_member_role_id="500", discord_leadership_role_id="500", org_name="Changed Name"),
        )
        assert resp.status_code == 200
        assert "member access role" in resp.content.decode()
        config = SiteConfiguration.load()
        assert config.discord_leadership_role_id == ""
        assert config.org_name != "Changed Name"

    def it_refuses_officers_equal_to_a_guild_member_role(admin_client: Client):
        GuildFactory(discord_role_ids=["600"], is_active=False)
        resp = admin_client.post(reverse("hub_admin_site_settings"), _post(discord_guild_officers_role_id="600"))
        assert resp.status_code == 200
        assert SiteConfiguration.load().discord_guild_officers_role_id == ""

    def it_refuses_a_lead_role_shared_by_two_guilds_on_the_row_and_saves_neither(admin_client: Client):
        first = GuildFactory(name="Alpha")
        second = GuildFactory(name="Beta")
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(
                **{
                    "guildroles-TOTAL_FORMS": "2",
                    "guildroles-INITIAL_FORMS": "2",
                    "guildroles-0-id": str(first.pk),
                    "guildroles-0-discord_role_ids": "",
                    "guildroles-0-discord_lead_role_id": "700",
                    "guildroles-1-id": str(second.pk),
                    "guildroles-1-discord_role_ids": "",
                    "guildroles-1-discord_lead_role_id": "700",
                }
            ),
        )
        assert resp.status_code == 200
        assert "Another guild already uses this lead role id." in resp.content.decode()
        first.refresh_from_db()
        assert first.discord_lead_role_id == ""

    def it_names_a_hidden_guild_whose_lead_role_clashes(admin_client: Client):
        GuildFactory(name="Hidden Hall", is_active=False, discord_lead_role_id="800")
        resp = admin_client.post(
            reverse("hub_admin_site_settings"), _post(discord_leadership_role_id="800"), follow=True
        )
        assert "Hidden Hall lead role id:" in resp.content.decode()
        assert SiteConfiguration.load().discord_leadership_role_id == ""

    def it_does_not_check_a_save_from_another_tab(admin_client: Client):
        config = SiteConfiguration.load()
        config.discord_member_role_id = "900"
        config.discord_leadership_role_id = "900"
        config.save()
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(submitted_tab="general", discord_member_role_id="900", discord_leadership_role_id="900"),
        )
        assert resp.status_code == 302

    def it_lets_a_visible_guild_reuse_a_hidden_guilds_old_lead_role_id(admin_client: Client):
        GuildFactory(name="Old Hall", is_active=False, discord_lead_role_id="950")
        guild = GuildFactory(name="New Hall")
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(
                **{
                    "guildroles-TOTAL_FORMS": "1",
                    "guildroles-INITIAL_FORMS": "1",
                    "guildroles-0-id": str(guild.pk),
                    "guildroles-0-discord_role_ids": "",
                    "guildroles-0-discord_lead_role_id": "950",
                }
            ),
        )
        assert resp.status_code == 302
        guild.refresh_from_db()
        assert guild.discord_lead_role_id == "950"


def describe_guild_lead_role_id():
    def it_saves_the_lead_role_id_in_the_guild_row(admin_client: Client):
        guild = GuildFactory()
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(
                **{
                    "guildroles-TOTAL_FORMS": "1",
                    "guildroles-INITIAL_FORMS": "1",
                    "guildroles-0-id": str(guild.pk),
                    "guildroles-0-discord_role_ids": "111",
                    "guildroles-0-discord_lead_role_id": "1121964994041499709",
                }
            ),
        )
        assert resp.status_code == 302
        guild.refresh_from_db()
        assert guild.discord_lead_role_id == "1121964994041499709"
        assert guild.discord_role_ids == ["111"]

    def it_refuses_a_lead_role_id_that_is_not_a_number(admin_client: Client):
        guild = GuildFactory()
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(
                **{
                    "guildroles-TOTAL_FORMS": "1",
                    "guildroles-INITIAL_FORMS": "1",
                    "guildroles-0-id": str(guild.pk),
                    "guildroles-0-discord_role_ids": "",
                    "guildroles-0-discord_lead_role_id": "lead",
                }
            ),
        )
        assert resp.status_code == 200
        guild.refresh_from_db()
        assert guild.discord_lead_role_id == ""
