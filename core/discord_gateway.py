"""Fog Bot's live Discord connection: a reaction on a role message becomes one durable task (#1270).

``python manage.py run_worker`` starts :func:`start_in_thread` and then hands the main thread to
``db_worker``. The thread runs a discord.py client with only the Guild Message Reactions intent
(not privileged) and no member or message cache. It never calls Discord's REST API and never
changes a role itself: for a reaction added or removed on a configured role message
(``SiteConfiguration.discord_role_message_ids``) with a mapped emoji (``DiscordGuildEmoji``),
:func:`dispatch` enqueues :func:`membership.discord_sync.apply_reaction`, so role work stays on
the serial queue with the leadership reconcile and survives a redeploy.

discord.py resumes and backs off on its own after a dropped connection. :func:`supervise` is the
outer guard: when the client raises (a bad token, an outage longer than its own retries), it
starts a new one after 5 seconds, doubling to 5 minutes, and logs an error once the gateway has
been down 10 minutes, which reaches Sentry. A reaction missed while it is down is added by the
15 minute reaction reconcile; a missed remove waits for that user's next reaction (accepted,
Felix 2026-11-20). A blank bot token (unset, or staging) starts nothing.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import discord
from django.db import close_old_connections

from core.events.discord_dm import bot_token

logger = logging.getLogger(__name__)

#: The first wait before restarting a client that raised, doubled after each failure.
FIRST_BACKOFF_SECONDS = 5.0
#: The longest wait between restarts.
MAX_BACKOFF_SECONDS = 300.0
#: How long the gateway may stay down before :func:`supervise` logs an error.
DOWN_ALERT_SECONDS = 600.0
#: A client that ran this long before raising counts as having been up, so the backoff starts over.
HEALTHY_RUN_SECONDS = 60.0


@dataclass(frozen=True)
class ReactionEvent:
    """One reaction the gateway saw, reduced to what :func:`dispatch` needs.

    Attributes:
        user_id: The reacting Discord user's id.
        channel_id: The channel of the reacted message.
        message_id: The reacted message.
        emoji: A unicode emoji, or a custom one as ``name:id``, the form ``DiscordGuildEmoji`` stores.
        added: ``True`` for an add, ``False`` for a remove.
    """

    user_id: str
    channel_id: str
    message_id: str
    emoji: str
    added: bool


def emoji_key(emoji: discord.PartialEmoji) -> str:
    """The ``DiscordGuildEmoji.emoji`` form of a gateway emoji: the character, or ``name:id`` when custom."""
    return f"{emoji.name}:{emoji.id}" if emoji.id else str(emoji.name)


def event_from_payload(payload: discord.RawReactionActionEvent, *, added: bool) -> ReactionEvent:
    """Reduce a raw reaction payload to a :class:`ReactionEvent`."""
    return ReactionEvent(
        user_id=str(payload.user_id),
        channel_id=str(payload.channel_id),
        message_id=str(payload.message_id),
        emoji=emoji_key(payload.emoji),
        added=added,
    )


def _matching_guild_id(emoji: str, mapping: dict[str, Any]) -> int | None:
    """The mapped guild's pk for ``emoji``, comparing without the U+FE0F variation selector too."""
    if emoji in mapping:
        return int(mapping[emoji].pk)
    bare = emoji.replace("️", "")
    for stored, guild in mapping.items():
        if stored.replace("️", "") == bare:
            return int(guild.pk)
    return None


def dispatch(event: ReactionEvent) -> bool:
    """Enqueue :func:`apply_reaction` for a reaction on a role message with a mapped emoji.

    Returns whether a task was queued; any other reaction on the server is ignored.
    """
    from core.models import SiteConfiguration
    from core.tasks import enqueue_on_commit
    from membership.discord_sync import apply_reaction
    from membership.models import DiscordGuildEmoji

    config = SiteConfiguration.load()
    if event.channel_id != (config.discord_role_message_channel_id or "").strip():
        return False
    if event.message_id not in config.discord_role_message_ids:
        return False
    guild_id = _matching_guild_id(event.emoji, DiscordGuildEmoji.objects.mapping())
    if guild_id is None:
        return False
    enqueue_on_commit(apply_reaction, event.user_id, guild_id, event.added)
    return True


def _dispatch_in_thread(event: ReactionEvent) -> bool:
    """:func:`dispatch` on a pool thread, dropping a stale or broken database connection either side."""
    close_old_connections()
    try:
        return dispatch(event)
    finally:
        close_old_connections()


class ReactionClient(discord.Client):
    """A discord.py client that only listens for reactions and hands each one to :func:`dispatch`."""

    def __init__(self) -> None:
        intents = discord.Intents.none()
        intents.guild_reactions = True
        super().__init__(
            intents=intents,
            member_cache_flags=discord.MemberCacheFlags.none(),
            max_messages=None,
            chunk_guilds_at_startup=False,
        )

    async def on_ready(self) -> None:
        logger.info("Discord gateway connected as %s.", self.user)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._hand_off(payload, added=True)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._hand_off(payload, added=False)

    async def _hand_off(self, payload: discord.RawReactionActionEvent, *, added: bool) -> None:
        """Skip the bot's own reactions, then queue the rest off the event loop."""
        if self.user is not None and payload.user_id == self.user.id:
            return
        try:
            await asyncio.to_thread(_dispatch_in_thread, event_from_payload(payload, added=added))
        except Exception:
            logger.exception("Discord gateway could not queue a reaction on message %s.", payload.message_id)


class _Client(Protocol):
    async def start(self, token: str) -> None: ...


async def supervise(
    token: str,
    make_client: Callable[[], _Client] = ReactionClient,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Run a client until it returns on its own, restarting it after each exception.

    A client's ``start`` returns only when it was closed on purpose, so a return ends the loop.
    After an exception the wait starts at :data:`FIRST_BACKOFF_SECONDS` and doubles up to
    :data:`MAX_BACKOFF_SECONDS`; a run that lasted :data:`HEALTHY_RUN_SECONDS` starts it over.
    Once the gateway has been down :data:`DOWN_ALERT_SECONDS` one error is logged per outage.
    """
    backoff = FIRST_BACKOFF_SECONDS
    down_since: float | None = None
    alerted = False
    while True:
        started = clock()
        try:
            await make_client().start(token)
            return
        except Exception as exc:
            failed = clock()
            if failed - started >= HEALTHY_RUN_SECONDS:
                backoff, down_since, alerted = FIRST_BACKOFF_SECONDS, None, False
            down_since = failed if down_since is None else down_since
            logger.warning("Discord gateway stopped (%s); restarting in %.0f s.", exc, backoff)
            if not alerted and failed - down_since >= DOWN_ALERT_SECONDS:
                logger.error(
                    "The Discord gateway has been down %.0f minutes: reactions give no roles.",
                    (failed - down_since) / 60,
                )
                alerted = True
        await sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


def start_in_thread() -> threading.Thread | None:
    """Start :func:`supervise` on a daemon thread, or nothing when the bot token is blank."""
    token = bot_token()
    if not token:
        logger.info("Discord gateway not started: the bot token is blank (unset, or staging).")
        return None
    thread = threading.Thread(target=asyncio.run, args=(supervise(token),), name="discord-gateway", daemon=True)
    thread.start()
    return thread
