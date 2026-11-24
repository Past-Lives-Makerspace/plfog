"""Site Settings, Discord tab: the managed role ids (#1148, #1270); per guild lead role ids are gone from the page."""

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
    def it_renders_each_on_the_discord_tab_once_and_no_lead_role_row(admin_client: Client):
        GuildFactory(name="Woodshop")
        content = admin_client.get(reverse("hub_admin_site_settings") + "?tab=discord").content.decode()
        for field_name in (
            "discord_leadership_role_id",
            "discord_guild_officers_role_id",
            "discord_guild_leads_role_id",
            "discord_tour_guides_role_id",
            "discord_instructors_role_id",
        ):
            assert content.count(f'name="{field_name}"') == 1
        assert 'name="guildroles-0-discord_role_ids"' in content
        assert "discord_lead_role_id" not in content

    def it_saves_every_id(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(
                discord_leadership_role_id=" 1534 ",
                discord_guild_officers_role_id="1535",
                discord_guild_leads_role_id="1536",
                discord_tour_guides_role_id="1537",
                discord_instructors_role_id="1538",
            ),
        )
        assert resp.status_code == 302
        config = SiteConfiguration.load()
        assert config.discord_leadership_role_id == "1534"
        assert config.discord_guild_officers_role_id == "1535"
        assert config.discord_guild_leads_role_id == "1536"
        assert config.discord_tour_guides_role_id == "1537"
        assert config.discord_instructors_role_id == "1538"

    @pytest.mark.parametrize(
        "field_name",
        [
            "discord_guild_officers_role_id",
            "discord_guild_leads_role_id",
            "discord_tour_guides_role_id",
            "discord_instructors_role_id",
        ],
    )
    @pytest.mark.parametrize("value", ["officers", "\u00b9\u00b2"])
    def it_refuses_an_id_that_is_not_a_number(admin_client: Client, field_name: str, value: str):
        resp = admin_client.post(reverse("hub_admin_site_settings"), _post(**{field_name: value}))
        assert resp.status_code == 200
        assert "A Discord role id is numbers only" in resp.content.decode()
        assert getattr(SiteConfiguration.load(), field_name) == ""


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

    def it_refuses_an_id_equal_to_a_hidden_guilds_member_role(admin_client: Client):
        GuildFactory(discord_role_ids=["600"], is_active=False)
        resp = admin_client.post(reverse("hub_admin_site_settings"), _post(discord_tour_guides_role_id="600"))
        assert resp.status_code == 200
        assert SiteConfiguration.load().discord_tour_guides_role_id == ""

    def it_judges_a_guild_member_role_as_posted(admin_client: Client):
        guild = GuildFactory(name="Alpha", discord_role_ids=["700"])
        rows = {
            "guildroles-TOTAL_FORMS": "1",
            "guildroles-INITIAL_FORMS": "1",
            "guildroles-0-id": str(guild.pk),
        }
        freed = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(**rows, **{"guildroles-0-discord_role_ids": "701"}, discord_instructors_role_id="700"),
        )
        assert freed.status_code == 302
        assert SiteConfiguration.load().discord_instructors_role_id == "700"
        taken = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(**rows, **{"guildroles-0-discord_role_ids": "702"}, discord_guild_leads_role_id="702"),
        )
        assert taken.status_code == 200
        assert "member access role" in taken.content.decode()
        guild.refresh_from_db()
        assert guild.discord_role_ids == ["701"]

    def it_refuses_two_roles_sharing_an_id_on_a_save_from_another_tab(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(
                submitted_tab="general",
                discord_leadership_role_id="910",
                discord_guild_leads_role_id="910",
                org_name="Changed Name",
            ),
        )
        assert resp.status_code == 200
        assert "This is already the Leadership role id." in resp.content.decode()
        assert "Fix the highlighted field on the Discord tab." in resp.content.decode()
        config = SiteConfiguration.load()
        assert config.discord_leadership_role_id == ""
        assert config.org_name != "Changed Name"

    def it_says_nothing_extra_when_the_error_is_on_the_tab_showing(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(submitted_tab="discord", discord_leadership_role_id="910", discord_guild_officers_role_id="910"),
        )
        assert resp.status_code == 200
        assert "Fix the highlighted field on the Discord tab." not in resp.content.decode()

    def it_never_judges_an_old_per_guild_lead_role_id(admin_client: Client):
        GuildFactory(name="Wood", discord_lead_role_id="920")
        resp = admin_client.post(
            reverse("hub_admin_site_settings"), _post(submitted_tab="general", discord_guild_leads_role_id="920")
        )
        assert resp.status_code == 302
        assert SiteConfiguration.load().discord_guild_leads_role_id == "920"


def describe_guild_rows():
    def it_saves_the_member_role_ids_and_leaves_the_old_lead_role_id_alone(admin_client: Client):
        guild = GuildFactory(discord_lead_role_id="1121964994041499709")
        resp = admin_client.post(
            reverse("hub_admin_site_settings"),
            _post(
                **{
                    "guildroles-TOTAL_FORMS": "1",
                    "guildroles-INITIAL_FORMS": "1",
                    "guildroles-0-id": str(guild.pk),
                    "guildroles-0-discord_role_ids": "111 112",
                    "guildroles-0-discord_lead_role_id": "",
                }
            ),
        )
        assert resp.status_code == 302
        guild.refresh_from_db()
        assert guild.discord_role_ids == ["111", "112"]
        assert guild.discord_lead_role_id == "1121964994041499709"
