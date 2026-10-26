"""Discord pings wait for review in Spotlight (#943): the intake behind ``queue_discord_pings``.

Every tick reads the server's text channels, then each channel's messages after its
:class:`~core.models.DiscordChannelCursor`. A person's message that pings @everyone, or a
role in a guild's ``discord_role_ids``, becomes a waiting
:class:`~core.models.QueuedDiscordMessage`, already drafted as a Spotlight line. Nothing goes
live here.

Skipped: bots and webhooks (plfog's own posts among them), @here alone, and channels hidden
from @everyone, except a guild's own channel for a ping of that guild. A channel's first
sighting records its newest message and queues nothing. A 429 that outlasts the one retry
stops the tick; the cursors already moved stay, and the next tick carries on from them.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.events.discord_dm import bot_disabled
from core.integrations.discord_channel import (
    MESSAGES_PAGE_LIMIT,
    DiscordRateLimitedError,
    channel_messages_after,
    discord_message_url,
    server_channels,
    server_roles,
)
from core.models import DiscordChannelCursor, QueuedDiscordMessage, SiteConfiguration
from membership.models import Guild, Member

logger = logging.getLogger(__name__)

#: Channel types read: a text channel and an announcement channel.
TEXT_CHANNEL_TYPES = (0, 5)
#: Message types read: a plain message and a reply. Joins, pins and boosts are not.
PERSON_MESSAGE_TYPES = (0, 19)
VIEW_CHANNEL = 1 << 10
#: Pages read per channel per tick; a busier channel carries on next tick.
MAX_PAGES_PER_CHANNEL = 5

_TOKEN_RE = re.compile(r"<(@&|@!?|#|a?:(\w+):)(\d+)>")
_EVERYONE_RE = re.compile(r"@everyone\b")
# A Discord timestamp, <t:UNIX> or <t:UNIX:STYLE>; every style reads as the same Pacific date and time.
_TIMESTAMP_RE = re.compile(r"<t:(-?\d+)(?::[tTdDfFR])?>")


def readable_time(match: re.Match[str]) -> str:
    """A Discord timestamp as a Pacific date and time: "Fri Oct 30, 7 PM", or "7:30 PM" off the hour."""
    moment = timezone.localtime(datetime.fromtimestamp(int(match.group(1)), tz=UTC))
    clock = moment.strftime("%-I %p") if moment.minute == 0 else moment.strftime("%-I:%M %p")
    return f"{moment:%a %b} {moment.day}, {clock}"


class _Server:
    """What one tick knows about the server: its id, guild roles, role names and channel names."""

    def __init__(self, server_id: str, channels: list[dict[str, Any]], roles: list[dict[str, Any]]) -> None:
        self.server_id = server_id
        self.channel_names = {channel["id"]: channel["name"] for channel in channels}
        self.role_names = {role["id"]: role["name"] for role in roles}
        self.guilds_by_role: dict[str, list[Guild]] = {}
        self.guild_channel_ids: set[str] = set()
        for guild in Guild.objects.filter(is_active=True).order_by("name"):
            self.guild_channel_ids.add(guild.discord_channel_id)
            for role_id in guild.discord_role_ids:
                self.guilds_by_role.setdefault(str(role_id), []).append(guild)

    def is_hidden(self, channel: dict[str, Any]) -> bool:
        """Whether @everyone (the role whose id is the server's) is denied View Channel.

        A channel synced to its category carries a copy of the category's overwrites, so its own
        list is the whole answer.
        """
        return any(
            overwrite["id"] == self.server_id and int(overwrite["deny"]) & VIEW_CHANNEL
            for overwrite in channel.get("permission_overwrites") or []
        )

    def readable(self, content: str, mentions: list[dict[str, Any]], ping_roles: set[str]) -> str:
        """``content`` with every Discord token as a name, and the triggering pings left out."""
        users = {user["id"]: sender_name(user) for user in mentions}

        def name(match: re.Match[str]) -> str:
            kind, emoji, snowflake = match.groups()
            if emoji:
                return f":{emoji}:"
            if kind == "@&":
                return "" if snowflake in ping_roles else f"@{self.role_names.get(snowflake, 'role')}"
            if kind == "#":
                return f"#{self.channel_names.get(snowflake, 'channel')}"
            return f"@{users.get(snowflake, 'someone')}"

        text = _EVERYONE_RE.sub("", _TIMESTAMP_RE.sub(readable_time, _TOKEN_RE.sub(name, content)))
        lines = (" ".join(line.split()) for line in text.splitlines())
        return "\n".join(line for line in lines if line)


def sender_name(user: dict[str, Any]) -> str:
    """A Discord user's display name, else their username."""
    return user.get("global_name") or user["username"]


def queue_discord_pings() -> int:
    """Read every readable channel's new messages and queue the pings; return how many were queued."""
    server_id = (SiteConfiguration.load().discord_server_id or "").strip()
    if not server_id or bot_disabled("ping intake"):
        return 0
    try:
        channels = [channel for channel in server_channels(server_id) if channel["type"] in TEXT_CHANNEL_TYPES]
        server = _Server(server_id, channels, server_roles(server_id))
        cursors = {cursor.channel_id: cursor for cursor in DiscordChannelCursor.objects.all()}
        empty: list[str] = []
        queued = sum(_read_channel(server, channel, cursors.get(channel["id"]), empty) for channel in channels)
    except DiscordRateLimitedError:
        logger.warning("queue_discord_pings: Discord rate limited this tick; the next tick carries on.")
        return 0
    if empty:
        logger.warning(
            "queue_discord_pings: %d ping(s) came back with no text, so nothing was queued for them. "
            "Is the bot's Message Content Intent on?",
            len(empty),
        )
    return queued


def _read_channel(
    server: _Server, channel: dict[str, Any], cursor: DiscordChannelCursor | None, empty: list[str]
) -> int:
    """Queue the pings in one channel after its cursor, moving the cursor page by page."""
    newest = channel.get("last_message_id") or "0"
    if cursor is None:
        DiscordChannelCursor.objects.create(channel_id=channel["id"], last_message_id=newest)
        return 0
    if server.is_hidden(channel) and channel["id"] not in server.guild_channel_ids:
        # Nothing in a hidden channel that is no guild's own is ever queued, so it is not read.
        cursor.last_message_id = newest
        cursor.save(update_fields=["last_message_id", "updated_at"])
        return 0
    queued = 0
    for _page in range(MAX_PAGES_PER_CHANNEL):
        if int(cursor.last_message_id) >= int(newest):
            break
        messages = channel_messages_after(channel["id"], cursor.last_message_id)
        if not messages:
            break
        with transaction.atomic():
            queued += sum(_queue(server, channel, message, empty) for message in messages)
            cursor.last_message_id = messages[-1]["id"]
            cursor.save(update_fields=["last_message_id", "updated_at"])
        if len(messages) < MESSAGES_PAGE_LIMIT:
            break
    return queued


def _queue(server: _Server, channel: dict[str, Any], message: dict[str, Any], empty: list[str]) -> bool:
    """Queue ``message`` when a person pinged @everyone or a guild role somewhere it may be read."""
    if message["author"].get("bot") or message.get("webhook_id") or message["type"] not in PERSON_MESSAGE_TYPES:
        return False
    content = message["content"]
    everyone = bool(message["mention_everyone"]) and (not content or bool(_EVERYONE_RE.search(content)))
    ping_roles = {role for role in message["mention_roles"] if role in server.guilds_by_role}
    guilds = list({guild.pk: guild for role in ping_roles for guild in server.guilds_by_role[role]}.values())
    if not everyone and not guilds:
        return False
    if server.is_hidden(channel) and not any(guild.discord_channel_id == channel["id"] for guild in guilds):
        return False
    if not content:
        empty.append(message["id"])
        return False
    text = server.readable(content, message["mentions"], ping_roles)
    return bool(text) and _create(server, channel, message, guilds, text)


def _create(server: _Server, channel: dict[str, Any], message: dict[str, Any], guilds: list[Guild], text: str) -> bool:
    """Write the waiting row with its drafted line; ``False`` when that message is already queued."""
    author = message["author"]
    member = Member.objects.filter(discord_user_id=author["id"]).first()
    name = sender_name(author)
    url = discord_message_url(server.server_id, channel["id"], message["id"])
    guild_names = sorted(guild.name.removesuffix(" Guild") for guild in guilds)
    row, created = QueuedDiscordMessage.objects.get_or_create(
        discord_message_id=message["id"],
        defaults={
            "discord_channel_id": channel["id"],
            "discord_url": url,
            "original_text": text,
            "line": QueuedDiscordMessage.draft_line(text, guild_names, member.display_name if member else name, url),
            "sender_discord_id": author["id"],
            "sender_name": name[:100],
            "sender": member,
            "sent_at": datetime.fromisoformat(message["timestamp"]),
        },
    )
    if created:
        row.guilds.set(guilds)
    return created
