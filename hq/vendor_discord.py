"""Check Discord (#1014): the table requests members post in the Holiday Bizarre channel, read on demand.

The vendor team presses Check Discord; nothing reads Discord on a schedule and nothing writes to it. The channel
mixes requests with organiser chat and cancellations, so a person decides each poster: Add as Request or Skip
(``hq.services.add_discord_poster`` and ``skip_discord_poster``).

:func:`read_channel` is the one read, and the only network call: the channel's name, its newest
:data:`MAX_MESSAGES` messages, and the server's members for their nicknames, through the Fog Bot
(``core.integrations.discord_channel``, so a 429 is retried once and any other failure raises). Specs replace it
(``patch.object(vendor_discord, "read_channel", ...)``; callers reach it through the module, so the patch reaches
the e2e live server too). :func:`new_posters` is the pure rule over what it read.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.utils import timezone

from core.discord_pings import PERSON_MESSAGE_TYPES
from core.events.discord_dm import bot_token
from core.integrations import discord_channel
from core.integrations.discord_channel import DiscordChannelError, DiscordRateLimitedError
from hq.vendor_import import _max_length, clean, normalise

# The most messages one check reads, newest first.
MAX_MESSAGES = 500
# Between a poster's posts in the note a request is added with.
NOTE_JOIN = " · "
NOTE_MAX = _max_length("note")

# Why the channel could not be read, in the vendor team's words.
NO_BOT = "The Fog Bot is not set up on this site, so Discord cannot be read."
CANNOT_SEE = "The Fog Bot cannot read that channel. In Discord, let it view the channel and read its history."
NO_CHANNEL = "Discord has no channel with that id. An admin can check it under Discord Channel on this page."
NO_MEMBER_LIST = "The Fog Bot cannot read the server's member list, so nicknames cannot be matched."
SLOW_DOWN = "Discord asked us to slow down. Try again in a minute."
NO_ANSWER = "Discord did not answer. Try again in a minute."

_MENTION = re.compile(r"<@!?([0-9]+)>")
_EMOJI = re.compile(r"<a?:(\w+):[0-9]+>")


class DiscordUnreadable(Exception):
    """The channel could not be read; the message says why, in the vendor team's words."""


@dataclass(frozen=True)
class ChannelRead:
    """What one check read: the channel's id and name, its newest messages as Discord sent them (newest first),
    and each server member's nickname by their Discord id (only members who set one)."""

    channel_id: str
    name: str
    messages: tuple[dict[str, Any], ...]
    nicks: Mapping[str, str]

    @property
    def label(self) -> str:
        """The channel as the panel names it: "#holiday-bizarre", or its id when it has no name."""
        return f"#{self.name or self.channel_id}"


def read_channel(channel_id: str) -> ChannelRead:
    """Read the channel through the Fog Bot: its name, its newest :data:`MAX_MESSAGES` messages, and the server's
    nicknames. The only network call here.

    Raises:
        DiscordUnreadable: no bot token on this site, Discord refused or could not find the channel or the member
            list, rate limited after the one retry, or no answer.
    """
    if not bot_token():
        raise DiscordUnreadable(NO_BOT)
    try:
        info = discord_channel.channel_info(channel_id)
        messages = discord_channel.channel_messages_newest(channel_id, limit=MAX_MESSAGES)
        try:
            members = discord_channel.server_members(info["guild_id"])
        except DiscordRateLimitedError:
            raise
        except DiscordChannelError as exc:
            raise DiscordUnreadable(NO_MEMBER_LIST) from exc
    except DiscordChannelError as exc:
        raise DiscordUnreadable(_why(exc)) from exc
    nicks = {member["user"]["id"]: member["nick"] for member in members if member.get("nick")}
    return ChannelRead(channel_id=channel_id, name=info.get("name") or "", messages=tuple(messages), nicks=nicks)


def _why(exc: DiscordChannelError) -> str:
    """A failed read in the vendor team's words, by Discord's answer."""
    if exc.status_code == 429:
        return SLOW_DOWN
    if exc.status_code == 403:
        return CANNOT_SEE
    if exc.status_code == 404:
        return NO_CHANNEL
    return NO_ANSWER


def display_name(user: Mapping[str, Any], nicks: Mapping[str, str]) -> str:
    """How the server shows a Discord user, as Kate's sheet writes their handle: their server nickname, else their
    display name, else their username; cleaned as a table's name is (``hq.vendor_import.clean``)."""
    return clean(nicks.get(user["id"]) or user.get("global_name") or user["username"])


def readable(content: str, mentions: Iterable[Mapping[str, Any]], nicks: Mapping[str, str]) -> str:
    """A message's text as people read it in Discord: a ``<@id>`` mention as @name (from the message's own
    ``mentions``, "@someone" for anyone it does not carry) and a custom emoji as :name:."""
    names = {user["id"]: display_name(user, nicks) for user in mentions}
    text = _MENTION.sub(lambda match: f"@{names.get(match.group(1), 'someone')}", content)
    return _EMOJI.sub(r":\1:", text).strip()


@dataclass(frozen=True)
class Post:
    """One Discord message on Check Discord: its id, when it was posted and its text as people read it."""

    message_id: str
    posted_at: datetime
    text: str


@dataclass(frozen=True)
class Poster:
    """One person on Check Discord, with their posts not yet added or skipped, newest first (never empty).

    ``name`` is how the server shows them (their nickname, else display name, else username), which is both the
    request's name and its Discord handle, as Kate's sheet writes it; ``username`` is their account's handle.
    """

    author_id: str
    name: str
    username: str
    posts: tuple[Post, ...]

    @property
    def message_ids(self) -> list[str]:
        return [post.message_id for post in self.posts]

    @property
    def first_posted_on(self) -> date:
        """The Portland date of their first listed post: the request's Requested on."""
        return timezone.localdate(self.posts[-1].posted_at)

    @property
    def note(self) -> str:
        """Their posts' text, oldest first, joined; cut with an ellipsis to fit the request's note."""
        text = NOTE_JOIN.join(" ".join(post.text.split()) for post in reversed(self.posts) if post.text)
        return text if len(text) <= NOTE_MAX else f"{text[: NOTE_MAX - 1]}…"

    def only(self, message_ids: Collection[str]) -> Poster:
        """This poster with only the posts whose ids are in ``message_ids`` (what the person pressing saw)."""
        return Poster(
            author_id=self.author_id,
            name=self.name,
            username=self.username,
            posts=tuple(post for post in self.posts if post.message_id in message_ids),
        )


def listed_keys(tables: Iterable[tuple[str, str]]) -> set[str]:
    """Every (name, Discord handle) pair on the list as the keys a poster matches: normalised
    (``hq.vendor_import.normalise``, so case, spacing and fullwidth letters do not matter), a handle's leading @
    dropped, blanks left out."""
    keys = {normalise(value.removeprefix("@")) for pair in tables for value in pair}
    keys.discard("")
    return keys


def new_posters(read: ChannelRead, *, listed: set[str], handled: Collection[str]) -> list[Poster]:
    """The people Check Discord lists, by their newest listed post, newest first: every author who is a person (no
    bot, webhook or system message such as a join), is not on the list (their username, display name or server
    nickname, normalised, is none of ``listed``), and has a post whose id is not in ``handled``. Each carries only
    those posts."""
    messages: dict[str, list[dict[str, Any]]] = {}
    for message in read.messages:
        author = message["author"]
        if author.get("bot") or message.get("webhook_id") or message["type"] not in PERSON_MESSAGE_TYPES:
            continue
        if message["id"] not in handled:
            messages.setdefault(author["id"], []).append(message)
    posters = []
    for author_id, theirs in messages.items():
        user = theirs[0]["author"]
        names = (user["username"], user.get("global_name") or "", read.nicks.get(author_id, ""))
        if any(normalise(name) in listed for name in names):
            continue
        posts = tuple(
            Post(
                message_id=message["id"],
                posted_at=datetime.fromisoformat(message["timestamp"]),
                text=readable(message["content"], message["mentions"], read.nicks),
            )
            for message in sorted(theirs, key=lambda message: int(message["id"]), reverse=True)
        )
        posters.append(
            Poster(author_id=author_id, name=display_name(user, read.nicks), username=user["username"], posts=posts)
        )
    return sorted(posters, key=lambda poster: int(poster.posts[0].message_id), reverse=True)
