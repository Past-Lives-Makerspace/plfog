"""Discord reactions reader — paging, the completeness flag, and the removal guardrail.

All Discord HTTP is mocked with ``respx`` (never the network). Covers the happy-path
single/multi-page read, the disabled (blank-config) no-op, and every failure mode that
must return ``complete=False`` so the reconcile never removes from a truncated read.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.events import discord_reactions

_REACTIONS_RE = r"https://discord\.com/api/v10/channels/.+/messages/.+/reactions/.+"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"


def describe_fetch_reactors():
    @respx.mock
    def it_returns_the_reactors_on_a_single_page():
        respx.get(url__regex=_REACTIONS_RE).mock(return_value=httpx.Response(200, json=[{"id": "111"}, {"id": "222"}]))
        page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        assert page.user_ids == {"111", "222"}
        assert page.complete is True

    @respx.mock
    def it_pages_until_a_short_page():
        full = [{"id": str(n)} for n in range(100)]
        respx.get(url__regex=_REACTIONS_RE).mock(
            side_effect=[httpx.Response(200, json=full), httpx.Response(200, json=[{"id": "999"}])]
        )
        page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        assert "999" in page.user_ids
        assert len(page.user_ids) == 101
        assert page.complete is True

    def it_no_ops_when_the_bot_token_is_blank(settings):
        settings.DISCORD_BOT_TOKEN = ""
        page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        assert page.user_ids == set()
        assert page.complete is False

    def it_no_ops_when_ids_are_blank():
        page = discord_reactions.fetch_reactors("", "", "🔥")
        assert page.complete is False

    @respx.mock
    def it_marks_incomplete_on_a_non_2xx():
        respx.get(url__regex=_REACTIONS_RE).mock(return_value=httpx.Response(500, text="boom"))
        page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        assert page.user_ids == set()
        assert page.complete is False

    @respx.mock
    def it_marks_incomplete_on_a_network_error():
        respx.get(url__regex=_REACTIONS_RE).mock(side_effect=httpx.ConnectError("down"))
        page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        assert page.complete is False

    @respx.mock
    def it_keeps_a_partial_page_but_marks_incomplete_on_a_mid_page_error():
        full = [{"id": str(n)} for n in range(100)]
        respx.get(url__regex=_REACTIONS_RE).mock(
            side_effect=[httpx.Response(200, json=full), httpx.Response(500, text="rate/err")]
        )
        page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        assert len(page.user_ids) == 100  # first page kept
        assert page.complete is False  # but not final

    @respx.mock
    def it_retries_a_429_then_succeeds():
        respx.get(url__regex=_REACTIONS_RE).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json=[{"id": "111"}]),
            ]
        )
        page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        assert page.user_ids == {"111"}
        assert page.complete is True

    @respx.mock
    def it_bails_incomplete_when_429_retries_are_exhausted():
        respx.get(url__regex=_REACTIONS_RE).mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))
        page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        assert page.complete is False

    @respx.mock
    def it_uses_a_default_retry_when_the_header_is_unparseable():
        # Non-numeric Retry-After → parsed to the small default; still bails after retries.
        respx.get(url__regex=_REACTIONS_RE).mock(return_value=httpx.Response(429, headers={"Retry-After": "soon"}))
        # Cap the sleep so the test stays fast even with the default wait.
        import core.events.discord_reactions as mod

        original = mod._MAX_RETRY_SLEEP_SECONDS
        mod._MAX_RETRY_SLEEP_SECONDS = 0.0
        try:
            page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        finally:
            mod._MAX_RETRY_SLEEP_SECONDS = original
        assert page.complete is False

    @respx.mock
    def it_stops_safely_when_a_full_page_has_no_last_id():
        # A full page whose entries carry no id can't be paged further → incomplete.
        respx.get(url__regex=_REACTIONS_RE).mock(return_value=httpx.Response(200, json=[{} for _ in range(100)]))
        page = discord_reactions.fetch_reactors("chan", "msg", "🔥")
        assert page.complete is False
