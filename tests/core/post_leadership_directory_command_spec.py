"""``post_leadership_directory``: dry run, --apply, --wipe and --adopt (#1149). HTTP is mocked with respx."""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import httpx
import pytest
import respx
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

CHANNEL = "968637078738976868"
CHANNEL_URL = f"https://discord.com/api/v10/channels/{CHANNEL}"
MESSAGES = f"{CHANNEL_URL}/messages"


def _named_channel() -> respx.Route:
    return respx.get(CHANNEL_URL).mock(return_value=httpx.Response(200, json={"name": "leadership-directory"}))


@pytest.fixture
def discord_on(settings) -> SiteConfiguration:
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_leadership_directory_channel_id = CHANNEL
    config.save()
    return config


def _run(*args: str) -> str:
    out = StringIO()
    call_command("post_leadership_directory", *args, stdout=out)
    return out.getvalue()


def _snowflake(days_ago: float) -> str:
    at = timezone.now() - timedelta(days=days_ago)
    return str((int(at.timestamp() * 1000) - 1420070400000) << 22)


def describe_post_leadership_directory():
    @respx.mock
    def it_prints_the_post_and_sends_nothing_by_default(discord_on):
        out = _run()
        assert "## 🙋 Leadership Directory" in out
        assert "Dry run: nothing sent." in out
        assert respx.calls.call_count == 0

    @respx.mock
    def it_counts_what_a_wipe_would_delete_without_deleting(discord_on):
        _named_channel()
        respx.get(MESSAGES).mock(return_value=httpx.Response(200, json=[{"id": "1"}, {"id": "2"}]))
        out = _run("--wipe")
        assert "Channel: #leadership-directory (968637078738976868)" in out
        assert "Would delete 2 messages in the channel." in out
        assert all(call.request.method == "GET" for call in respx.calls)

    @respx.mock
    def it_sends_nothing_with_a_blank_channel_or_bot_token(settings):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        assert "Nothing sent" in _run("--wipe", "--apply")
        settings.DISCORD_BOT_TOKEN = ""
        config = SiteConfiguration.load()
        config.discord_leadership_directory_channel_id = CHANNEL
        config.save()
        assert "Nothing sent" in _run("--wipe", "--apply")
        assert respx.calls.call_count == 0

    @respx.mock
    def it_applies_by_editing_the_stored_post_even_when_unchanged(discord_on):
        discord_on.discord_leadership_directory_message_ids = ["501"]
        discord_on.discord_leadership_directory_hash = "matches"
        discord_on.save()
        edit = respx.patch(f"{MESSAGES}/501").mock(return_value=httpx.Response(200, json={}))
        out = _run("--apply")
        assert edit.call_count == 1
        assert "Posted. Message ids: 501" in out
        assert SiteConfiguration.load().discord_leadership_directory_hash not in ("", "matches")

    @respx.mock
    def it_wipes_the_channel_then_posts_fresh(discord_on):
        discord_on.discord_leadership_directory_message_ids = ["old"]
        discord_on.save()
        recent, older = [_snowflake(1), _snowflake(2)], [_snowflake(30)]
        _named_channel()
        respx.get(MESSAGES).mock(return_value=httpx.Response(200, json=[{"id": mid} for mid in recent + older]))
        bulk = respx.post(f"{MESSAGES}/bulk-delete").mock(return_value=httpx.Response(204))
        single = respx.delete(f"{MESSAGES}/{older[0]}").mock(return_value=httpx.Response(204))
        post = respx.post(MESSAGES).mock(return_value=httpx.Response(200, json={"id": "900"}))
        out = _run("--wipe", "--apply")
        assert json.loads(bulk.calls[0].request.content) == {"messages": recent}
        assert single.call_count == 1 and post.call_count == 1
        assert "Channel: #leadership-directory" in out
        assert "Deleted 3 messages." in out
        assert SiteConfiguration.load().discord_leadership_directory_message_ids == ["900"]

    @respx.mock
    def it_adopts_an_existing_post_and_edits_it_in_place(discord_on):
        edit = respx.patch(f"{MESSAGES}/1569596431428227155").mock(return_value=httpx.Response(200, json={}))
        _run("--adopt", "1569596431428227155", "--apply")
        assert edit.call_count == 1
        assert json.loads(edit.calls[0].request.content)["allowed_mentions"] == {"parse": []}
        assert SiteConfiguration.load().discord_leadership_directory_message_ids == ["1569596431428227155"]

    @respx.mock
    def it_only_reports_an_adopt_without_apply(discord_on):
        assert "Would adopt messages 11, 12" in _run("--adopt", "11,12")
        assert SiteConfiguration.load().discord_leadership_directory_message_ids == []

    def it_refuses_bad_adopt_ids_and_adopt_with_wipe(discord_on):
        with pytest.raises(CommandError, match="numbers only"):
            _run("--adopt", "abc")
        with pytest.raises(CommandError, match="not both"):
            _run("--adopt", "11", "--wipe")

    @respx.mock
    def it_fails_the_command_when_discord_fails(discord_on):
        respx.post(MESSAGES).mock(return_value=httpx.Response(500, text="down"))
        with pytest.raises(CommandError, match="could not be updated"):
            _run("--apply")

    @respx.mock
    def it_refuses_to_wipe_a_channel_it_cannot_read(discord_on):
        respx.get(CHANNEL_URL).mock(return_value=httpx.Response(403, text="Missing Access"))
        with pytest.raises(CommandError, match="Could not read channel"):
            _run("--wipe", "--apply")
        assert all(call.request.method == "GET" for call in respx.calls)

    @respx.mock
    def it_refuses_to_wipe_a_channel_with_no_name(discord_on):
        respx.get(CHANNEL_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(CommandError, match="has no name"):
            _run("--wipe")
