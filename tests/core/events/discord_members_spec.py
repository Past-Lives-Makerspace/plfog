"""Discord guild-members reader — paging, the completeness flag, and payload parsing.

All Discord HTTP is mocked with ``respx`` (never the network). Covers the happy-path
single/multi-page read, the disabled (blank-config) no-op, every failure mode that must
return ``complete=False`` with whatever was gathered, and the payload gotchas: Discord
OMITS ``user.bot`` for humans (absent means "not a bot"), and a missing/garbled
``joined_at`` parses to ``None`` instead of crashing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from core.events import discord_members

_MEMBERS_RE = r"https://discord\.com/api/v10/guilds/.+/members.*"
_JOINED = "2026-08-12T10:00:00+00:00"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"


def _entry(user_id: str, *, joined_at: str | None = _JOINED, **user_extra):
    entry: dict = {"user": {"id": user_id, **user_extra}}
    if joined_at is not None:
        entry["joined_at"] = joined_at
    return entry


def describe_fetch_guild_members():
    @respx.mock
    def it_returns_the_members_on_a_single_page():
        respx.get(url__regex=_MEMBERS_RE).mock(return_value=httpx.Response(200, json=[_entry("111"), _entry("222")]))
        page = discord_members.fetch_guild_members("srv")
        assert [m.user_id for m in page.members] == ["111", "222"]
        assert page.complete is True

    @respx.mock
    def it_treats_an_absent_bot_flag_as_human_and_a_present_one_as_bot():
        # Discord OMITS user.bot for humans — absent is NOT false-y data loss, it IS "not a bot".
        respx.get(url__regex=_MEMBERS_RE).mock(
            return_value=httpx.Response(200, json=[_entry("111"), _entry("222", bot=True)])
        )
        page = discord_members.fetch_guild_members("srv")
        assert [(m.user_id, m.bot) for m in page.members] == [("111", False), ("222", True)]

    @respx.mock
    def it_parses_joined_at_and_maps_missing_or_garbled_values_to_none():
        respx.get(url__regex=_MEMBERS_RE).mock(
            return_value=httpx.Response(
                200,
                json=[_entry("111"), _entry("222", joined_at=None), _entry("333", joined_at="not-a-date")],
            )
        )
        page = discord_members.fetch_guild_members("srv")
        assert page.members[0].joined_at == datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
        assert page.members[1].joined_at is None
        assert page.members[2].joined_at is None

    @respx.mock
    def it_pages_until_a_short_page():
        full = [_entry(str(n)) for n in range(1000)]
        respx.get(url__regex=_MEMBERS_RE).mock(
            side_effect=[httpx.Response(200, json=full), httpx.Response(200, json=[_entry("9999")])]
        )
        page = discord_members.fetch_guild_members("srv")
        assert len(page.members) == 1001
        assert page.members[-1].user_id == "9999"
        assert page.complete is True

    def it_no_ops_when_the_bot_token_is_blank(settings):
        settings.DISCORD_BOT_TOKEN = ""
        page = discord_members.fetch_guild_members("srv")
        assert page.members == []
        assert page.complete is False

    def it_no_ops_when_the_server_id_is_blank():
        page = discord_members.fetch_guild_members("")
        assert page.members == []
        assert page.complete is False

    @respx.mock
    def it_marks_incomplete_on_a_non_2xx():
        respx.get(url__regex=_MEMBERS_RE).mock(return_value=httpx.Response(500, text="boom"))
        page = discord_members.fetch_guild_members("srv")
        assert page.members == []
        assert page.complete is False

    @respx.mock
    def it_marks_incomplete_on_a_network_error():
        respx.get(url__regex=_MEMBERS_RE).mock(side_effect=httpx.ConnectError("down"))
        page = discord_members.fetch_guild_members("srv")
        assert page.complete is False

    @respx.mock
    def it_keeps_a_partial_page_but_marks_incomplete_on_a_mid_page_error():
        full = [_entry(str(n)) for n in range(1000)]
        respx.get(url__regex=_MEMBERS_RE).mock(
            side_effect=[httpx.Response(200, json=full), httpx.Response(500, text="rate/err")]
        )
        page = discord_members.fetch_guild_members("srv")
        assert len(page.members) == 1000  # first page kept — safe to act on, the ledger dedupes
        assert page.complete is False

    @respx.mock
    def it_retries_a_429_then_succeeds():
        respx.get(url__regex=_MEMBERS_RE).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json=[_entry("111")]),
            ]
        )
        page = discord_members.fetch_guild_members("srv")
        assert [m.user_id for m in page.members] == ["111"]
        assert page.complete is True

    @respx.mock
    def it_bails_incomplete_with_partials_when_429_retries_are_exhausted():
        full = [_entry(str(n)) for n in range(1000)]
        rate_limited = httpx.Response(429, headers={"Retry-After": "0"})
        respx.get(url__regex=_MEMBERS_RE).mock(
            side_effect=[httpx.Response(200, json=full), rate_limited, rate_limited, rate_limited]
        )
        page = discord_members.fetch_guild_members("srv")
        assert len(page.members) == 1000  # the page before the rate-limit is kept
        assert page.complete is False

    @respx.mock
    def it_stops_safely_when_a_full_page_has_no_last_user_id():
        # A full page whose last entry carries no user id can't be paged further → incomplete.
        respx.get(url__regex=_MEMBERS_RE).mock(return_value=httpx.Response(200, json=[{} for _ in range(1000)]))
        page = discord_members.fetch_guild_members("srv")
        assert page.members == []
        assert page.complete is False
