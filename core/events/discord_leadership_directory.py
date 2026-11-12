"""The #leadership-directory Discord post mirrors the app's Leadership Directory (#1149).

FOG owns one or more messages in the channel named by
``SiteConfiguration.discord_leadership_directory_channel_id`` (blank turns all of this off,
and so does a blank bot token, which is how staging stays dark). They read like the web page
(``hub.views.leadership_directory``): a header embed with the page's title and lead line,
one embed per People tab with someone listed, and the Guild Leads embed, one line group per
active guild (never the example guild, whatever ``display_demo_guild`` says).

* :func:`render_directory_payloads` is the renderer: it sends nothing, its one Discord call
  is a read of the server's member list (:class:`Usernames`), Discord's limits are kept
  (a section too long for one embed continues in the next, and a message too full continues
  in the next message), and every payload carries ``allowed_mentions`` with an empty
  ``parse``, so the guild lead role mentions never ping anyone. A member whose Discord handle
  is not public shows as a bare name; otherwise as their Discord username linked to their
  profile, read through their verified ``discord_user_id`` (``Member.link_discord``), else
  their typed handle in plain text when it looks like a username. Never a user mention:
  inside an embed, Discord shows one as the display name, or "unknown-user" to anyone whose
  app has not loaded that member.
  Member and admin text is escaped so it cannot build a link or mention.
  Each guild shows its lead role mention and contact email when set; a People tab its intro.
* :func:`sync_leadership_directory` is the task: it renders, and when the content hash equals
  the stored one and every stored message is still there it sends nothing; otherwise it edits
  the stored messages in order, posts any extra, deletes any surplus, and stores the ids and
  hash. A stored message that is gone (404) means a fresh set is posted. A Postgres advisory
  lock serializes syncs, so two workers cannot both post.
* :func:`directory_on_commit` is what every change calls: one enqueue per transaction, at
  most one waiting sync in the queue, run about :data:`COALESCE_SECONDS` later so an admin's
  burst of autosaves ends in one set of edits.

The ``post_leadership_directory`` command renders, wipes the channel and adopts an existing
post (:func:`wipe_channel`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.tasks import TaskResultStatus, task
from django.urls import reverse
from django.utils import timezone

from core.events.discord_dm import bot_disabled
from core.integrations.discord_channel import (
    BULK_DELETE_MAX,
    DiscordChannelError,
    bulk_delete_channel_messages,
    channel_message_exists,
    channel_messages_newest,
    delete_channel_message,
    edit_channel_message,
    post_channel_message,
    server_members,
)
from core.tasks import enqueue_on_commit

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.tasks import Task

    from membership.models import Guild, LeadershipTab, Member

logger = logging.getLogger(__name__)

#: Discord's caps: one embed's description, all text across one message's embeds, embeds per message.
EMBED_DESCRIPTION_MAX = 4096
MESSAGE_TEXT_MAX = 6000
EMBEDS_PER_MESSAGE_MAX = 10
#: Every post and edit sends this, so a mention never pings anyone.
NO_MENTIONS: dict[str, list[str]] = {"parse": []}
#: How long a sync waits after the first change, so a burst of saves ends in one set of edits.
COALESCE_SECONDS = 10

HEADER_COLOR = 0xE07A5F
PEOPLE_COLOR = 0xF2CC8F
GUILDS_COLOR = 0x81B29A
HEADER_EMOJI = "🙋"
PEOPLE_EMOJI = "✨"
GUILDS_EMOJI = "🛠️"
NO_LEAD_LINE = "*Looking for a lead!*"
#: Where a verified account's username links: their profile, opened in the Discord app.
PROFILE_URL = "https://discord.com/users/{user_id}"
#: The header line when someone is shown by a typed handle, which is plain text, not a link.
SEARCH_LINE = "Search a handle on Discord to message them."
FOOTER_SUFFIX = "Kept current from the member app"

#: A guild's heading emoji, by the start of its name; anything else gets :data:`GUILDS_EMOJI`.
GUILD_EMOJI = {
    "Art Framing": "🖼️",
    "Ceramics": "🏺",
    "ERMA": "🆘",
    "Events": "🎉",
    "Food Independence": "🌱",
    "Gardeners": "🌻",
    "Glass": "🔮",
    "Jewelry": "💍",
    "Leatherwork": "👜",
    "Metalworkers": "⚒️",
    "Music": "🎵",
    "Printmaking": "🖨️",
    "Prison Outreach": "🤝",
    "Tech": "💻",
    "Textiles": "🧵",
    "Visual Arts": "🎨",
    "Woodworking": "🪚",
    "Writers": "✒️",
}

#: Bulk delete refuses messages older than 14 days; anything this close to the line goes one by one.
BULK_DELETE_AGE = timedelta(days=14) - timedelta(hours=1)
#: The most messages a wipe reads from the channel.
WIPE_READ_LIMIT = 5000
_DISCORD_EPOCH_MS = 1420070400000


class LeadershipDirectorySyncError(Exception):
    """A Discord call the sync needed failed; raised at the end so the task is recorded FAILED and alerts."""


# --- Rendering ---


def _size(text: str) -> int:
    """Length as Discord counts it at most (UTF-16 units), so an emoji never tips a payload over a cap."""
    return len(text.encode("utf-16-le")) // 2


#: Characters escaped in member and admin text: markdown, and what could build a masked link,
#: a mention (``<@id>``, ``<@&id>``), a heading or an ``@everyone``.
_ESCAPED = ("\\", "*", "_", "~", "`", "|", "[", "]", "(", ")", "<", ">", "#", "@")
#: A typed handle shows only when, after normalizing, it looks like a Discord username.
_HANDLE_RE = re.compile(r"[A-Za-z0-9._]{2,32}")


def _escape(text: str) -> str:
    """``text`` with every :data:`_ESCAPED` character backslash escaped, so it renders as typed and nothing more."""
    for char in _ESCAPED:
        text = text.replace(char, f"\\{char}")
    return text


def public_handle(member: Member) -> str:
    """The member's typed Discord handle as ``@handle``, or "" when it is hidden, blank or not a username.

    Normalized first: spaces removed and leading ``@`` dropped. Shown only when the privacy
    settings make it public and it matches :data:`_HANDLE_RE`.
    """
    handle = re.sub(r"\s+", "", member.discord_handle or "").lstrip("@")
    if not _HANDLE_RE.fullmatch(handle) or not member.is_public("discord_handle"):
        return ""
    return f"@{_escape(handle)}"


class Usernames:
    """Discord usernames by user id, read from the server's member list once, the first time one is asked for.

    So a render reads the list only when it shows a verified account, and never with a blank
    ``SiteConfiguration.discord_server_id`` or bot token; then every answer is "". An id not
    in the server (the member left it) is "" too. A failed read raises
    :class:`DiscordChannelError`, so a sync fails loudly rather than posting names it could
    not read.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, str] | None = None

    def __call__(self, user_id: str) -> str:
        """The username for ``user_id``, or "" when it cannot be known."""
        if self._by_id is None:
            self._by_id = self._read()
        return self._by_id.get(user_id, "")

    @staticmethod
    def _read() -> dict[str, str]:
        from core.events.discord_leadership_roles import server_id

        server = server_id()
        if not server or bot_disabled("leadership directory usernames"):
            return {}
        return {str(entry["user"]["id"]): str(entry["user"]["username"]) for entry in server_members(server)}


@dataclass(frozen=True)
class Person:
    """A person as the post shows them, and whether that includes a handle to search for."""

    text: str
    shows_handle: bool = False


def who(member: Member, usernames: Callable[[str], str]) -> Person:
    """The name in bold, then their Discord handle when their privacy settings allow it.

    As on the web card (``_discord_row.html``): a member whose Discord handle is not public
    shows the bare name. Otherwise a verified account shows its username from ``usernames`` as
    a link to their Discord profile (:data:`PROFILE_URL`), and without one (or when the account
    is not in the server) a typed handle shows as plain text when it looks like a username.
    Never a mention, which Discord shows as the display name.
    """
    name = f"**{_escape(member.display_name.strip())}**"
    if not member.is_public("discord_handle"):
        return Person(name)
    user_id = (member.discord_user_id or "").strip()
    username = usernames(user_id) if user_id.isdigit() else ""
    if username:
        return Person(f"{name} [@{_escape(username)}]({PROFILE_URL.format(user_id=user_id)})")
    handle = public_handle(member)
    if handle:
        return Person(f"{name} {handle}", shows_handle=True)
    return Person(name)


def guild_emoji(name: str) -> str:
    """The heading emoji for a guild, by the start of its name."""
    return next((emoji for prefix, emoji in GUILD_EMOJI.items() if name.startswith(prefix)), GUILDS_EMOJI)


@dataclass
class Section:
    """One part of the post: a heading, a color and its entries, joined by a blank line.

    A section longer than one embed continues in untitled embeds of the same color.
    ``shows_handle`` is whether any person in it is shown by a typed handle.
    """

    title: str
    color: int
    entries: list[str] = field(default_factory=list)
    shows_handle: bool = False


def _header_section(*, handles_shown: bool) -> Section:
    """The page title and lead line from ``LeadershipPage``, the search line when a handle shows, and the web link."""
    from membership.models import LeadershipPage

    page = LeadershipPage.load()
    url = f"{settings.MEMBER_BASE_URL}{reverse('hub_leadership_directory')}"
    search = SEARCH_LINE if handles_shown else ""
    lead = "\n".join(line for line in (page.hero_lead.strip(), search) if line)
    return Section(
        title=f"{HEADER_EMOJI} {page.hero_title.strip()}",
        color=HEADER_COLOR,
        entries=[lead, f"📖 **Full directory with photos:** {url}"],
    )


def _people_section(tab: LeadershipTab, usernames: Callable[[str], str]) -> Section:
    """A People tab: each listed person, their role titles, and the roles' emails when there are any."""
    entries = []
    shows_handle = False
    for listing in tab.listed_listings:
        roles = list(listing.roles.all())
        person = who(listing.member, usernames)
        shows_handle = shows_handle or person.shows_handle
        lines = [person.text]
        titles = " · ".join(_escape(role.title) for role in roles)
        emails = " · ".join(_escape(role.email) for role in roles if role.email)
        if titles:
            lines.append(titles)
        if emails:
            lines.append(f"✉️ {emails}")
        entries.append("\n".join(lines))
    if entries and tab.intro.strip():
        entries.insert(0, f"*{_escape(tab.intro.strip())}*")
    title = f"{PEOPLE_EMOJI} {tab.title.strip()}"
    return Section(title=title, color=PEOPLE_COLOR, entries=entries, shows_handle=shows_handle)


def _guild_leads(guild: Guild, usernames: Callable[[str], str]) -> list[Person]:
    """The guild's lead, then its Co-Leads."""
    people = [who(guild.guild_lead, usernames)] if guild.guild_lead is not None else []
    return people + [who(member, usernames) for member in guild.co_leads]


def _guild_entry(guild: Guild, people: list[Person]) -> str:
    """A guild's emoji, name and lead role mention (when configured); its leads; its contact email (when set)."""
    role_id = (guild.discord_lead_role_id or "").strip()
    heading = f"{guild_emoji(guild.name)} **{_escape(guild.name)}**"
    if role_id.isdigit():
        heading += f" <@&{role_id}>"
    lines = [heading, " & ".join(person.text for person in people) if people else NO_LEAD_LINE]
    email = (guild.contact_email or "").strip()
    if email:
        lines.append(f"✉️ {_escape(email)}")
    return "\n".join(lines)


def _guilds_section(tab: LeadershipTab, usernames: Callable[[str], str]) -> Section:
    """The Guild Leads tab: every active guild in name order, its lead and Co-Leads or the looking for a lead line.

    The example guild is never active, so it never shows.
    """
    from membership.models import Guild

    guilds = (
        Guild.objects.filter(is_active=True)
        .select_related("guild_lead")
        .prefetch_related("staff_memberships__member")
        .order_by("name")
    )
    entries = []
    shows_handle = False
    for guild in guilds:
        people = _guild_leads(guild, usernames)
        shows_handle = shows_handle or any(person.shows_handle for person in people)
        entries.append(_guild_entry(guild, people))
    title = f"{GUILDS_EMOJI} {tab.title.strip()}"
    return Section(title=title, color=GUILDS_COLOR, entries=entries, shows_handle=shows_handle)


def directory_sections() -> list[Section]:
    """The header, then each tab the web page shows a member, in admin order; an empty one is left out."""
    from membership.models import LeadershipTab

    usernames = Usernames()
    tabs = []
    for tab in LeadershipTab.objects.for_directory(include_empty=False):
        section = _guilds_section(tab, usernames) if tab.is_guild_leads else _people_section(tab, usernames)
        if section.entries:
            tabs.append(section)
    return [_header_section(handles_shown=any(section.shows_handle for section in tabs)), *tabs]


def _pieces(entry: str) -> list[str]:
    """``entry`` as one piece, or, when it alone outgrows an embed, its lines (a line too long is cut)."""
    if _size(entry) <= EMBED_DESCRIPTION_MAX:
        return [entry]
    pieces: list[str] = []
    for line in entry.split("\n"):
        while _size(line) > EMBED_DESCRIPTION_MAX:
            cut = EMBED_DESCRIPTION_MAX // 2  # UTF-16 can count a character twice; half always fits
            pieces.append(line[:cut])
            line = line[cut:]
        pieces.append(line)
    return pieces


def section_embeds(section: Section) -> list[dict[str, Any]]:
    """The embeds for one section: entries packed into descriptions within the cap, the title on the first."""
    descriptions: list[str] = []
    current = ""
    for piece in (piece for entry in section.entries for piece in _pieces(entry)):
        joined = f"{current}\n\n{piece}" if current else piece
        if current and _size(joined) > EMBED_DESCRIPTION_MAX:
            descriptions.append(current)
            joined = piece
        current = joined
    descriptions.append(current)
    embeds = [{"description": text, "color": section.color} for text in descriptions]
    embeds[0] = {"title": section.title, **embeds[0]}
    return embeds


def _embed_size(embed: dict[str, Any]) -> int:
    """The text of an embed that counts toward a message's cap."""
    footer = embed.get("footer", {}).get("text", "")
    return _size(embed.get("title", "")) + _size(embed.get("description", "")) + _size(footer)


def pack_messages(embeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group embeds into message payloads: at most 10 embeds and 6000 characters each, order kept."""
    messages: list[list[dict[str, Any]]] = []
    used = 0
    for embed in embeds:
        size = _embed_size(embed)
        if not messages or len(messages[-1]) >= EMBEDS_PER_MESSAGE_MAX or used + size > MESSAGE_TEXT_MAX:
            messages.append([])
            used = 0
        messages[-1].append(embed)
        used += size
    return [{"embeds": chunk, "allowed_mentions": NO_MENTIONS} for chunk in messages]


def directory_embeds() -> list[dict[str, Any]]:
    """The post's embeds in order, without the footer: the content the hash covers."""
    return [embed for section in directory_sections() for embed in section_embeds(section)]


def content_hash(embeds: list[dict[str, Any]]) -> str:
    """SHA256 of the embeds' JSON with sorted keys: equal content, equal hash. The footer date stays out."""
    return hashlib.sha256(json.dumps(embeds, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def footer_text(day: date) -> str:
    """The last embed's footer: the date the content last changed, in words."""
    return f"Updated {day:%B} {day.day}, {day.year} · {FOOTER_SUFFIX}"


def with_footer(embeds: list[dict[str, Any]], day: date) -> list[dict[str, Any]]:
    """``embeds`` packed into message payloads, the last embed carrying the footer for ``day``."""
    footed = [dict(embed) for embed in embeds]
    footed[-1]["footer"] = {"text": footer_text(day)}
    return pack_messages(footed)


def render_directory_payloads(day: date | None = None) -> list[dict[str, Any]]:
    """The directory as Discord message payloads, in order. Sends nothing; reads the database and :class:`Usernames`.

    Each payload is ``{"embeds": [...], "allowed_mentions": {"parse": []}}`` within Discord's
    limits; the last embed carries the "Updated" footer for ``day``, by default the stored
    ``discord_leadership_directory_updated_on`` (the Pacific date the content last changed),
    or today when none is stored.
    """
    from core.models import SiteConfiguration

    day = day or SiteConfiguration.load().discord_leadership_directory_updated_on or timezone.localdate()
    return with_footer(directory_embeds(), day)


def payload_text(payloads: list[dict[str, Any]]) -> str:
    """The payloads as plain text, for the command's dry run."""
    blocks = []
    for number, payload in enumerate(payloads, start=1):
        blocks.append(f"===== Message {number} of {len(payloads)}")
        for embed in payload["embeds"]:
            if "title" in embed:
                blocks.append(f"## {embed['title']}")
            blocks.append(embed["description"])
            if "footer" in embed:
                blocks.append(f"-- {embed['footer']['text']}")
    return "\n".join(blocks)


# --- Sending ---


def channel_id() -> str:
    """The configured directory channel; blank turns the post off."""
    from core.models import SiteConfiguration

    return (SiteConfiguration.load().discord_leadership_directory_channel_id or "").strip()


class _MessageGone(Exception):
    """A stored message answered 404: the set is posted fresh."""


class _Publisher:
    """Makes the channel's messages match the payloads, tracking which ids exist as it goes.

    ``ids`` is always what the channel holds as best known, so a failure part way stores it
    and the next sync carries on from there instead of leaving a stray post behind.
    """

    def __init__(self, channel: str, stored: list[str]) -> None:
        self.channel = channel
        self.ids = list(stored)

    def run(self, payloads: list[dict[str, Any]]) -> None:
        """Edit in place, post the extra, delete the surplus; post a fresh set when one is gone."""
        try:
            self._edit_in_place(payloads)
        except _MessageGone:
            self._delete_from(0)
            for payload in payloads:
                self.ids.append(self._post(payload))

    def _edit_in_place(self, payloads: list[dict[str, Any]]) -> None:
        for index, payload in enumerate(payloads):
            if index >= len(self.ids):
                self.ids.append(self._post(payload))
                continue
            try:
                edit_channel_message(
                    self.channel, self.ids[index], payload["embeds"], allowed_mentions=payload["allowed_mentions"]
                )
            except DiscordChannelError as exc:
                if exc.status_code == 404:
                    raise _MessageGone from exc
                raise
        self._delete_from(len(payloads))

    def _delete_from(self, keep: int) -> None:
        """Delete every stored message after the first ``keep``, last first; one already gone is fine."""
        while len(self.ids) > keep:
            delete_channel_message(self.channel, self.ids[-1])
            self.ids.pop()

    def _post(self, payload: dict[str, Any]) -> str:
        message = post_channel_message(
            self.channel, payload["embeds"], allowed_mentions=payload["allowed_mentions"], timeout=15.0
        )
        message_id = str(message.get("id") or "")
        if not message_id:
            raise DiscordChannelError("Discord answered a post with no message id.")
        return message_id


def _all_present(channel: str, stored: list[str]) -> bool:
    """Whether there is a stored set and every message in it is still in the channel."""
    return bool(stored) and all(channel_message_exists(channel, message_id) for message_id in stored)


#: The Postgres advisory lock key that serializes directory syncs ("plfog-1149" as a number).
SYNC_LOCK_KEY = 1149_0001


def _hold_sync_lock() -> None:
    """Take the transaction scoped advisory lock that lets one sync run at a time; nothing off Postgres.

    A lock on its own key, not on the ``SiteConfiguration`` row, so the Discord calls a sync
    makes never block a Site Settings save. Released at the end of the sync's transaction.
    """
    connection = transaction.get_connection()
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [SYNC_LOCK_KEY])


@task
def sync_leadership_directory() -> None:
    """Bring the Discord post in step with the Leadership Directory. Safe to run twice.

    Sends nothing with a blank channel id or bot token, and nothing when the content hash
    (the embeds without the footer) equals the stored one and every stored message is still
    there. When the content changed from a known hash, the footer date becomes today (Pacific)
    and is stored in ``discord_leadership_directory_updated_on``; otherwise (a gone message,
    a forced send with the hash cleared, a first post) the stored date stays, or today when
    none is stored. Holds a Postgres advisory lock (:func:`_hold_sync_lock`) from the hash check
    to the save, so a second sync running at once waits and then finds nothing to do; the
    ``SiteConfiguration`` row itself is never locked across the Discord calls, and only the
    three sync state columns are written, so a Site Settings save in between is never undone.

    Raises:
        LeadershipDirectorySyncError: A Discord call failed. Raised after the ids that exist
            were stored (with a blank hash, so the next sync sends again), so the task is
            recorded FAILED and alerts.
    """
    from core.models import SiteConfiguration

    channel = channel_id()
    if not channel or bot_disabled("leadership directory sync"):
        return
    failure: DiscordChannelError | None = None
    with transaction.atomic():
        _hold_sync_lock()
        config = SiteConfiguration.load()
        embeds = directory_embeds()
        digest = content_hash(embeds)
        previous = config.discord_leadership_directory_hash
        stored = [str(message_id) for message_id in config.discord_leadership_directory_message_ids or []]
        if digest == previous and _all_present(channel, stored):
            return
        changed = bool(previous) and digest != previous
        day = config.discord_leadership_directory_updated_on
        if changed or day is None:
            day = timezone.localdate()
        publisher = _Publisher(channel, stored)
        try:
            publisher.run(with_footer(embeds, day))
        except DiscordChannelError as exc:
            failure = exc
        SiteConfiguration.objects.filter(pk=config.pk).update(
            discord_leadership_directory_message_ids=publisher.ids,
            discord_leadership_directory_hash="" if failure else digest,
            discord_leadership_directory_updated_on=day,
        )
    if failure is not None:
        raise LeadershipDirectorySyncError(f"The leadership directory post could not be updated: {failure}")


# --- Coalescing ---


def _deferred_sync() -> Task[[], None]:
    """The sync task, set to run :data:`COALESCE_SECONDS` from now where the backend can wait (not under test)."""
    if sync_leadership_directory.get_backend().supports_defer:
        return sync_leadership_directory.using(run_after=timezone.now() + timedelta(seconds=COALESCE_SECONDS))
    return sync_leadership_directory


def sync_waiting() -> bool:
    """Whether a sync is already in the queue and not started; a later change then needs no other."""
    from django_tasks_db.models import DBTaskResult

    return DBTaskResult.objects.filter(
        status=TaskResultStatus.READY, task_path=sync_leadership_directory.module_path
    ).exists()


class _SyncWhenCommitted:
    """The on-commit callback, recognisable so a second change in the transaction can find it.

    ``__qualname__`` is set on the instance because Django names a robust callback in its
    log line when it raises, and an instance has none of its own.
    """

    def __init__(self) -> None:
        self.fired = False
        self.__qualname__ = "sync_leadership_directory_on_commit"

    def __call__(self) -> None:
        self.fired = True
        if channel_id() and not sync_waiting():
            enqueue_on_commit(_deferred_sync())


def _registered() -> bool:
    """Whether this transaction already has a sync waiting on its commit.

    Reads Django's internal ``run_on_commit`` list, as ``reconcile_on_commit`` does; if its
    shape changes this answers False, and the cost is a second enqueue that the queue check
    and the hash make harmless.
    """
    try:
        return any(
            isinstance(func, _SyncWhenCommitted) and not func.fired
            for _sids, func, _robust in transaction.get_connection().run_on_commit
        )
    except Exception:  # noqa: BLE001 - dedupe is an optimisation; plain registration is always correct.
        return False


def directory_on_commit() -> None:
    """Sync the Discord post shortly after this transaction commits; call it from anything the post shows.

    At most one callback per transaction, and none enqueued when a sync is already waiting
    in the queue or the channel id is blank. Both are checked when the callback runs, so
    inside a transaction a trigger costs no query until the commit; outside one Django runs
    the callback at once, and those two queries run then. A rolled back save enqueues nothing.
    """
    if not _registered():
        transaction.on_commit(_SyncWhenCommitted(), robust=True)


# --- Wipe ---


def message_time(message_id: str) -> datetime:
    """When a Discord message was sent, read from its id (a snowflake)."""
    millis = (int(message_id) >> 22) + _DISCORD_EPOCH_MS
    return datetime.fromtimestamp(millis / 1000, tz=timezone.get_current_timezone())


def channel_message_ids(channel: str) -> list[str]:
    """Every message id in the channel, newest first (up to :data:`WIPE_READ_LIMIT`)."""
    return [str(message["id"]) for message in channel_messages_newest(channel, limit=WIPE_READ_LIMIT)]


def wipe_channel(channel: str, message_ids: list[str], *, now: datetime) -> int:
    """Delete ``message_ids`` from the channel; return how many.

    Messages younger than :data:`BULK_DELETE_AGE` go in bulk deletes of 2 to 100; a lone
    one left over and every older one go one by one, because Discord refuses those in bulk.
    """
    recent = [mid for mid in message_ids if now - message_time(mid) < BULK_DELETE_AGE]
    young = set(recent)
    single = [mid for mid in message_ids if mid not in young]
    for start in range(0, len(recent), BULK_DELETE_MAX):
        chunk = recent[start : start + BULK_DELETE_MAX]
        if len(chunk) >= 2:
            bulk_delete_channel_messages(channel, chunk)
        else:
            single.extend(chunk)
    for message_id in single:
        delete_channel_message(channel, message_id)
    return len(message_ids)
