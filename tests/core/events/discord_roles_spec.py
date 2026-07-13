"""Outbound Discord role sync — assign/remove and the ``on_membership_changed`` hook.

All Discord HTTP is mocked with ``respx``. Covers the raw REST helpers (2xx, disabled,
benign 403/404, network error, never raising) and the entry point's guards + lockstep
multi-role behavior.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.events import discord_roles

_ROLE_RE = r"https://discord\.com/api/v10/guilds/.+/members/.+/roles/.+"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"


def describe_assign_role():
    @respx.mock
    def it_puts_and_returns_true_on_success():
        route = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        assert discord_roles.assign_role("srv", "user", "role") is True
        assert route.called

    def it_no_ops_when_the_token_is_blank(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert discord_roles.assign_role("srv", "user", "role") is False

    def it_no_ops_on_a_blank_id():
        assert discord_roles.assign_role("srv", "", "role") is False

    @respx.mock
    def it_treats_403_as_benign_without_raising():
        respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(403, text="missing perms"))
        assert discord_roles.assign_role("srv", "user", "role") is False

    @respx.mock
    def it_treats_404_as_benign_without_raising():
        respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(404, text="member gone"))
        assert discord_roles.assign_role("srv", "user", "role") is False

    @respx.mock
    def it_returns_false_on_a_500():
        respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(500, text="boom"))
        assert discord_roles.assign_role("srv", "user", "role") is False

    @respx.mock
    def it_returns_false_on_a_network_error():
        respx.put(url__regex=_ROLE_RE).mock(side_effect=httpx.ConnectError("down"))
        assert discord_roles.assign_role("srv", "user", "role") is False


def describe_remove_role():
    @respx.mock
    def it_deletes_and_returns_true_on_success():
        route = respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        assert discord_roles.remove_role("srv", "user", "role") is True
        assert route.called


def describe_on_membership_changed():
    @pytest.fixture
    def _linked_guild(db):
        from core.models import SiteConfiguration
        from tests.membership.factories import GuildFactory, MemberFactory

        config = SiteConfiguration.load()
        config.discord_server_id = "srv-1"
        config.save()
        guild = GuildFactory(discord_role_ids=["role-a", "role-b"])
        member = MemberFactory(discord_user_id="user-1")
        return guild, member

    @respx.mock
    def it_assigns_every_role_on_join_lockstep(_linked_guild):
        guild, member = _linked_guild
        route = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        discord_roles.on_membership_changed(guild, member, joined=True)
        assert route.call_count == 2  # both Glass roles, in lockstep

    @respx.mock
    def it_removes_every_role_on_leave_lockstep(_linked_guild):
        guild, member = _linked_guild
        route = respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        discord_roles.on_membership_changed(guild, member, joined=False)
        assert route.call_count == 2

    @respx.mock
    def it_no_ops_when_the_member_is_not_linked(_linked_guild, db):
        from tests.membership.factories import MemberFactory

        guild, _member = _linked_guild
        route = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        discord_roles.on_membership_changed(guild, MemberFactory(discord_user_id=""), joined=True)
        assert not route.called

    @respx.mock
    def it_no_ops_when_the_guild_has_no_role_ids(db):
        from core.models import SiteConfiguration
        from tests.membership.factories import GuildFactory, MemberFactory

        config = SiteConfiguration.load()
        config.discord_server_id = "srv-1"
        config.save()
        route = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        discord_roles.on_membership_changed(
            GuildFactory(discord_role_ids=[]), MemberFactory(discord_user_id="user-1"), joined=True
        )
        assert not route.called

    @respx.mock
    def it_no_ops_when_the_server_id_is_blank(db):
        from tests.membership.factories import GuildFactory, MemberFactory

        route = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        discord_roles.on_membership_changed(
            GuildFactory(discord_role_ids=["role-a"]), MemberFactory(discord_user_id="user-1"), joined=True
        )
        assert not route.called

    @respx.mock
    def it_never_raises_even_on_a_discord_500(_linked_guild):
        guild, member = _linked_guild
        respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(500))
        # Must not raise — the in-app join already committed.
        discord_roles.on_membership_changed(guild, member, joined=True)

    def it_swallows_an_unexpected_config_error(_linked_guild, monkeypatch):
        # Even a config-load hiccup can never fail the in-app join/leave.
        guild, member = _linked_guild
        monkeypatch.setattr(
            "core.models.SiteConfiguration.load", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        )
        discord_roles.on_membership_changed(guild, member, joined=True)  # no raise
