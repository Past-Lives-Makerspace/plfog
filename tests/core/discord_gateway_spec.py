"""BDD specs for core.discord_gateway (#1270, part 3): the live reaction listener and its supervisor.

No live Discord: payloads are plain objects, the client is a fake, and the queue is the
immediate backend or a patched dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import discord
import pytest

from core import discord_gateway
from core.discord_gateway import ReactionEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def role_message(settings):
    from core.models import SiteConfiguration

    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv"
    config.discord_role_message_channel_id = "chan"
    config.discord_role_message_id = "msg"
    config.discord_role_message_extra_ids = "msg2"
    config.save()
    return config


def _event(**overrides: object) -> ReactionEvent:
    fields: dict[str, object] = {
        "user_id": "u1",
        "channel_id": "chan",
        "message_id": "msg",
        "emoji": "🔥",
        "added": True,
    }
    fields.update(overrides)
    return ReactionEvent(**fields)  # type: ignore[arg-type]


def describe_event_from_payload():
    def it_reads_a_unicode_and_a_custom_emoji_in_the_stored_form():
        unicode = SimpleNamespace(user_id=1, channel_id=2, message_id=3, emoji=discord.PartialEmoji(name="🔥"))
        custom = SimpleNamespace(user_id=1, channel_id=2, message_id=3, emoji=discord.PartialEmoji(name="Glass", id=99))
        assert discord_gateway.event_from_payload(unicode, added=True) == ReactionEvent("1", "2", "3", "🔥", True)  # type: ignore[arg-type]
        assert discord_gateway.event_from_payload(custom, added=False).emoji == "Glass:99"  # type: ignore[arg-type]


def describe_dispatch():
    @pytest.fixture
    def glass(role_message):
        from tests.membership.factories import DiscordGuildEmojiFactory, GuildFactory

        guild = GuildFactory(name="Glass")
        DiscordGuildEmojiFactory(emoji="❤️", guild=guild)
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        return guild

    def it_queues_one_task_for_a_mapped_emoji_on_either_role_message(glass):
        seen = datetime(2026, 11, 23, 17, 0, tzinfo=UTC)
        with (
            patch("membership.discord_sync.apply_reaction") as task,
            patch("core.tasks.enqueue_on_commit") as enqueue,
            patch("django.utils.timezone.now", return_value=seen),
        ):
            assert discord_gateway.dispatch(_event()) is True
            assert discord_gateway.dispatch(_event(message_id="msg2", added=False)) is True
        assert [c.args[1:] for c in enqueue.call_args_list] == [
            ("u1", glass.pk, True, seen.isoformat()),
            ("u1", glass.pk, False, seen.isoformat()),
        ]
        assert enqueue.call_args.args[0] is task

    def it_matches_an_emoji_sent_without_its_variation_selector(glass):
        with patch("core.tasks.enqueue_on_commit") as enqueue:
            assert discord_gateway.dispatch(_event(emoji="❤")) is True
        assert enqueue.call_args.args[2] == glass.pk

    def it_ignores_other_channels_other_messages_and_unmapped_emojis(glass):
        with patch("core.tasks.enqueue_on_commit") as enqueue:
            assert discord_gateway.dispatch(_event(channel_id="general")) is False
            assert discord_gateway.dispatch(_event(message_id="other")) is False
            assert discord_gateway.dispatch(_event(emoji="🪓")) is False
        assert not enqueue.called


def describe_ReactionClient():
    def it_listens_for_reactions_only_with_no_member_cache():
        client = discord_gateway.ReactionClient()
        assert client.intents.value == discord.Intents(guild_reactions=True).value
        assert client._connection.member_cache_flags.value == 0

    def it_hands_adds_and_removes_off_the_loop_and_skips_its_own_reactions():
        client = discord_gateway.ReactionClient()
        payload = SimpleNamespace(user_id=5, channel_id=2, message_id=3, emoji=discord.PartialEmoji(name="🔥"))
        own = SimpleNamespace(user_id=42, channel_id=2, message_id=3, emoji=discord.PartialEmoji(name="🔥"))
        seen: list[ReactionEvent] = []

        async def run() -> None:
            await client.on_ready()
            await client.on_raw_reaction_add(payload)  # type: ignore[arg-type]
            await client.on_raw_reaction_remove(payload)  # type: ignore[arg-type]
            with patch.object(discord.Client, "user", SimpleNamespace(id=42)):
                await client.on_raw_reaction_add(own)  # type: ignore[arg-type]

        with (
            patch.object(discord_gateway, "dispatch", side_effect=seen.append),
            patch.object(discord_gateway, "close_old_connections") as close,
        ):
            asyncio.run(run())
        assert [e.added for e in seen] == [True, False]
        assert seen[0].user_id == "5"
        assert close.call_count == 4

    def it_logs_a_reaction_it_could_not_queue_and_keeps_listening(caplog):
        client = discord_gateway.ReactionClient()
        payload = SimpleNamespace(user_id=5, channel_id=2, message_id=3, emoji=discord.PartialEmoji(name="🔥"))
        with (
            patch.object(discord_gateway, "dispatch", side_effect=RuntimeError("db down")),
            patch.object(discord_gateway, "close_old_connections"),
            caplog.at_level(logging.ERROR, logger="core.discord_gateway"),
        ):
            asyncio.run(client.on_raw_reaction_add(payload))  # type: ignore[arg-type]
        assert "could not queue a reaction on message 3" in caplog.text


class _FakeClient:
    """Raises each exception in ``plan`` on successive starts, then returns."""

    def __init__(self, plan: list[Exception], durations: list[float], clock: list[float]) -> None:
        self.plan, self.durations, self.clock = plan, durations, clock

    async def start(self, token: str) -> None:
        self.clock[0] += self.durations.pop(0) if self.durations else 0.0
        if self.plan:
            raise self.plan.pop(0)


def _supervise(plan: list[Exception], durations: list[float]) -> tuple[list[float], int]:
    clock = [0.0]
    sleeps: list[float] = []
    starts = 0

    def make() -> _FakeClient:
        nonlocal starts
        starts += 1
        return fake

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    fake = _FakeClient(plan, durations, clock)
    asyncio.run(discord_gateway.supervise("tok", make, sleep=sleep, clock=lambda: clock[0]))
    return sleeps, starts


def describe_supervise():
    def it_restarts_a_client_that_raised_then_stops_when_one_returns():
        sleeps, starts = _supervise([ConnectionError("gateway gone")], [])
        assert (sleeps, starts) == ([5.0], 2)

    def it_doubles_the_wait_up_to_five_minutes_and_logs_one_error_after_ten_minutes_down(caplog):
        with caplog.at_level(logging.WARNING, logger="core.discord_gateway"):
            sleeps, starts = _supervise([OSError("down")] * 9, [])
        assert sleeps == [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0, 300.0]
        assert starts == 10
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "down 10 minutes" in errors[0].getMessage()

    def it_starts_the_backoff_over_after_a_client_that_ran_a_while():
        sleeps, _starts = _supervise([OSError("a"), OSError("b"), OSError("c")], [0.0, 0.0, 120.0])
        assert sleeps == [5.0, 10.0, 5.0]


def describe_start_in_thread():
    def it_starts_nothing_with_a_blank_bot_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert discord_gateway.start_in_thread() is None

    def it_runs_the_supervisor_on_a_daemon_thread(settings):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        tokens: list[str] = []

        async def fake_supervise(token: str) -> None:
            tokens.append(token)

        with patch.object(discord_gateway, "supervise", fake_supervise):
            thread = discord_gateway.start_in_thread()
            assert thread is not None
            thread.join(timeout=5)
        assert thread.daemon is True
        assert tokens == ["bot-tok"]
