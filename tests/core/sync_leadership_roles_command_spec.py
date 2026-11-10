"""``manage.py sync_leadership_roles`` (#1148): a dry run lists the drift, ``--apply`` fixes it.

Discord HTTP is mocked with ``respx``; the member list is one paged ``GET``.
"""

from __future__ import annotations

from io import StringIO

import httpx
import pytest
import respx
from django.core.management import CommandError, call_command

from core.models import SiteConfiguration
from membership.models import GuildStaffMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipListingFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

API = "https://discord.com/api/v10"
LIST_URL = f"{API}/guilds/srv/members"


@pytest.fixture
def configured(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv"
    config.discord_leadership_role_id = "111"
    config.discord_guild_officers_role_id = "222"
    config.save()
    return config


@pytest.fixture
def drift(configured):
    """Amber leads Wood with no roles; Terlouw holds Gardeners Lead and leads nothing; an admin holds leadership."""
    amber = MemberFactory(full_legal_name="Amber Capwell", discord_user_id="10")
    GuildFactory(name="Woodworkers", guild_lead=amber, discord_lead_role_id="333")
    GuildFactory(name="Gardeners", discord_lead_role_id="444")
    terlouw = MemberFactory(full_legal_name="Amber Terlouw", discord_user_id="20")
    admin = MemberFactory(full_legal_name="Lee Admin", discord_user_id="30", fog_role=Member.FogRole.ADMIN)
    settled = MemberFactory(full_legal_name="Sam Settled", discord_user_id="40")
    GuildStaffMembershipFactory(member=settled, role=GuildStaffMembership.Role.TREASURER)
    unlinked = MemberFactory(full_legal_name="Una Unlinked")
    LeadershipListingFactory(member=unlinked)
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"user": {"id": "10"}, "roles": ["999"]},
                {"user": {"id": "20"}, "roles": ["444", "999"]},
                {"user": {"id": "30"}, "roles": ["111"]},
                {"user": {"id": "40"}, "roles": ["222"]},
            ],
        )
    )
    return {"amber": amber, "terlouw": terlouw, "admin": admin}


def _run(*args: str) -> str:
    out = StringIO()
    call_command("sync_leadership_roles", *args, stdout=out)
    return out.getvalue()


def describe_dry_run():
    @respx.mock
    def it_lists_each_differing_member_by_name_and_changes_nothing(drift):
        writes = respx.route(method__in=["PUT", "DELETE"]).mock(return_value=httpx.Response(204))
        out = _run()
        assert "Amber Capwell (10): add leadership, Guild Officers, Woodworkers Lead" in out
        assert "Amber Terlouw (20): remove Gardeners Lead" in out
        assert "Lee Admin (30): remove leadership" in out
        assert "Sam Settled" not in out
        assert "Should hold a role but have not linked Discord:\n  Una Unlinked" in out
        assert "Dry run: 3 members differ" in out
        assert not writes.called

    @respx.mock
    def it_names_a_linked_member_who_is_not_on_the_server(configured):
        member = MemberFactory(full_legal_name="Gone Away", discord_user_id="77")
        GuildFactory(guild_lead=member)
        respx.get(LIST_URL).mock(return_value=httpx.Response(200, json=[]))
        assert "Gone Away (77): not on the Discord server, skipped." in _run()

    @respx.mock
    def it_leaves_out_an_unlinked_member_who_earns_no_configured_role(configured):
        configured.discord_leadership_role_id = ""
        configured.discord_guild_officers_role_id = ""
        configured.save()
        GuildFactory(name="Has a lead role", discord_lead_role_id="333")
        GuildFactory(name="No lead role", guild_lead=MemberFactory(full_legal_name="Nora Norole"))
        respx.get(LIST_URL).mock(return_value=httpx.Response(200, json=[]))
        assert "Nora Norole" not in _run()

    @respx.mock
    def it_says_so_when_everyone_matches(configured):
        respx.get(LIST_URL).mock(return_value=httpx.Response(200, json=[]))
        assert "already match" in _run()

    def it_stops_when_the_server_id_is_blank(configured):
        configured.discord_server_id = ""
        configured.save()
        assert "sync is off" in _run()

    def it_stops_when_no_role_id_is_set(configured):
        configured.discord_leadership_role_id = ""
        configured.discord_guild_officers_role_id = ""
        configured.save()
        assert "nothing to sync" in _run()

    @respx.mock
    def it_fails_loudly_when_the_member_list_cannot_be_read(configured):
        respx.get(LIST_URL).mock(return_value=httpx.Response(403, text="intent off"))
        with pytest.raises(CommandError, match="member list"):
            _run()


def describe_unlinked_holders():
    @pytest.fixture
    def strangers(configured):
        MemberFactory(full_legal_name="Pat Pending", discord_handle="@Pat.Lead")
        MemberFactory(full_legal_name="Only At", discord_handle="@")
        respx.get(LIST_URL).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"user": {"id": "66", "username": "ghost"}, "roles": ["111", "999"]},
                    {"user": {"id": "77", "username": "pat.lead"}, "roles": ["222"]},
                ],
            )
        )

    @respx.mock
    def it_lists_each_with_their_username_and_marks_a_handle_match(strangers):
        out = _run()
        assert "Discord account ghost (66), no linked member: remove leadership" in out
        assert "Discord account pat.lead (77), no linked member (matches unlinked app member Pat Pending): " in out

    @respx.mock
    def it_leaves_them_alone_on_a_plain_apply(strangers):
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        _run("--apply")
        assert not delete.called

    @respx.mock
    def it_strips_the_unmatched_ones_with_strip_unlinked(strangers):
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        out = _run("--apply", "--strip-unlinked")
        assert [call.request.url.path for call in delete.calls] == ["/api/v10/guilds/srv/members/66/roles/111"]
        assert "Added 0 roles and removed 1." in out

    @respx.mock
    def it_matches_a_legacy_tag_a_display_name_and_a_nickname(configured):
        MemberFactory(full_legal_name="Tag Holder", discord_handle="OldTag#1234")
        MemberFactory(full_legal_name="Rosa Rivera", preferred_name="")
        MemberFactory(full_legal_name="Nick Named", discord_handle="woodnick")
        respx.get(LIST_URL).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"user": {"id": "81", "username": "oldtag"}, "roles": ["111"]},
                    {"user": {"id": "82", "username": "rr22", "global_name": "Rosa Rivera"}, "roles": ["111"]},
                    {
                        "user": {"id": "83", "username": "zz", "global_name": None},
                        "nick": "@WoodNick",
                        "roles": ["111"],
                    },
                    {"user": {"id": "84", "username": "nobody"}, "roles": ["111"]},
                ],
            )
        )
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        out = _run("--apply", "--strip-unlinked")
        assert "oldtag (81), no linked member (matches unlinked app member Tag Holder)" in out
        assert "rr22 (82), no linked member (matches unlinked app member Rosa Rivera)" in out
        assert "zz (83), no linked member (matches unlinked app member Nick Named)" in out
        assert [call.request.url.path for call in delete.calls] == ["/api/v10/guilds/srv/members/84/roles/111"]

    def it_refuses_strip_unlinked_without_apply(configured):
        with pytest.raises(CommandError, match="needs --apply"):
            _run("--strip-unlinked")


def describe_failures():
    @respx.mock
    def it_reports_failed_changes_in_the_summary(drift, monkeypatch):
        from core.events import discord_roles

        monkeypatch.setattr(discord_roles, "_sleep", lambda seconds: None)
        respx.put(url__regex=r".+").mock(return_value=httpx.Response(429))
        respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        out = _run("--apply")
        assert "Added 0 roles and removed 2." in out
        assert "3 role changes failed; see the log, then run --apply again." in out

    @respx.mock
    def it_counts_a_403_as_a_failure(drift):
        respx.put(url__regex=r".+").mock(return_value=httpx.Response(403))
        respx.delete(url__regex=r".+").mock(return_value=httpx.Response(403))
        assert "5 role changes failed" in _run("--apply")

    def it_stops_when_the_bot_token_is_blank(configured, settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert "bot token is blank" in _run()


def describe_apply():
    @respx.mock
    def it_makes_the_changes_and_counts_them(drift):
        put = respx.put(url__regex=rf"{API}/guilds/srv/members/\d+/roles/\d+").mock(return_value=httpx.Response(204))
        delete = respx.delete(url__regex=rf"{API}/guilds/srv/members/\d+/roles/\d+").mock(
            return_value=httpx.Response(204)
        )
        out = _run("--apply")
        assert sorted(call.request.url.path for call in put.calls) == [
            f"/api/v10/guilds/srv/members/10/roles/{role}" for role in ("111", "222", "333")
        ]
        assert sorted(call.request.url.path for call in delete.calls) == [
            "/api/v10/guilds/srv/members/20/roles/444",
            "/api/v10/guilds/srv/members/30/roles/111",
        ]
        assert "Added 3 roles and removed 2." in out
