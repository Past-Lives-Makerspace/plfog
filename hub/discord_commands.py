"""The hub app's member slash commands: ``/link`` (connect) and ``/create-announcement``.

``/link`` is the highest-leverage command: it's how a member in Discord connects their
account to Past Lives, which gates *every* other part of the integration (guild sync, DMs,
and every other slash command). The command itself makes **no state change** — it hands the
member the one-tap web OAuth connect link (which links only on a verified-email match), or,
if they're already linked, a friendly "you're all set". Linking happens exclusively inside
the vetted OAuth flow behind that link, never here.

``requires_link=False`` is essential: ``/link``'s whole audience is *unlinked* members, so
the platform's auto-connect gate must not intercept it — the handler does its own
linked/unlinked branching. It replies ephemerally in three cases: integration not
configured, already linked, and not-yet-linked (the connect link). Any unexpected exception
is turned into the friendly error reply by :func:`core.events.discord_commands.dispatch`'s
per-command guard, so Discord never sees a 500.

``/link`` mirrors the built-in ``/fog-ping`` reference command in :mod:`core.events.discord_commands`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from core.events.discord_commands import ComponentHandler, SlashCommand, register, register_component
from core.events.discord_interactions import error_reply, reply, update_message
from core.events.discord_replies import hub_url, option_value, truncate

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from core.events.discord_commands import Interaction
    from membership.models import AnnouncementDraft, Guild, Member

logger = logging.getLogger(__name__)

# --- Reply copy (spec §6 States 1-3) ------------------------------------------

_NOT_CONFIGURED = (
    "Connecting Discord isn't set up right now. Please try again later, or reach out to a Past Lives organizer."
)
_ALREADY_LINKED = (
    "You're already connected as **{display_name}** — you're all set. "
    "See what's coming up on the Calendar: {calendar_url}"
)
_CONNECT = (
    "Let's connect your Discord to your Past Lives account. **[Connect Discord]({connect_url})**"
    "\n\nAfter you approve, we'll match you by your verified email, link you automatically, and set you up in "
    "your guilds. It only takes a few seconds."
)


def _connect_url() -> str:
    """The absolute one-tap ``hub_discord_link_start`` URL handed to an unlinked member.

    Built from :data:`settings.MEMBER_BASE_URL` (the handler has no request), mirroring how
    the ``/fog-ping`` reference command builds its absolute hub link.
    """
    from django.conf import settings
    from django.urls import reverse

    return f"{settings.MEMBER_BASE_URL}{reverse('hub_discord_link_start')}"


def _link(interaction: Interaction, member: Member | None) -> dict:
    """Serve the caller their connect state — never links anyone (the OAuth flow does).

    ``requires_link=False`` means dispatch calls this for *every* caller, passing ``member``
    as the linked :class:`~membership.models.Member` for the caller's Discord id or ``None``
    if they aren't linked yet. The three ephemeral outcomes mirror spec §6 States 1-3.
    """
    from core.events import discord_oauth

    if not discord_oauth.is_configured():
        return reply(_NOT_CONFIGURED, ephemeral=True)

    if member is not None:
        return reply(
            _ALREADY_LINKED.format(display_name=member.display_name, calendar_url=hub_url("hub_community_calendar")),
            ephemeral=True,
        )

    connect_url = _connect_url()
    button_row = {
        "type": 1,
        "components": [
            {"type": 2, "style": 5, "label": "Connect Discord", "url": connect_url},
        ],
    }
    return reply(_CONNECT.format(connect_url=connect_url), ephemeral=True, components=[button_row])


LINK = SlashCommand(
    name="link",
    description="Connect your Discord to your Past Lives account.",
    handler=_link,
    requires_link=False,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(LINK)


# --- /create-announcement -----------------------------------------------------
#
# Post an announcement to Discord + the in-app bell (never email — the email fan-out has
# no rate cap, so v1 keeps this Discord + in-app only, ``send_email=False`` throughout).
# Authority mirrors the hub composer: a SITE announcement needs ``is_fog_admin``; a GUILD
# one needs ``can_edit_guild`` — a member with neither is pointed at the hub's propose flow
# and NOTHING is posted. Any ``@here`` / ``@everyone`` / ``@role`` ping is gated behind a
# two-step EPHEMERAL confirm (the Discord equivalent of the web wizard's Step-2 preview):
# the slash reply is a preview with Confirm / Cancel buttons, and the post fires only on the
# ``MESSAGE_COMPONENT`` callback. The message + audience live on a persisted
# :class:`~membership.models.AnnouncementDraft` (a slash ``custom_id`` caps at 100 chars —
# too small for a message body); the short ping choice rides in the ``custom_id`` itself.

_ANNOUNCE_AUDIENCE_SITE = "site"
_ANNOUNCE_MENTIONS = ("none", "here", "everyone", "role")
# The two-step confirm flow only ever pings — a no-ping post fires immediately and never
# mints a confirm button, so a `:none` confirm must be rejected (it would KeyError the display).
_ANNOUNCE_PING_MENTIONS = ("here", "everyone", "role")
_ANNOUNCE_TITLE_LIMIT = 120  # the headline derived from the message's first line
_ANNOUNCE_PREVIEW_TITLE_LIMIT = 240
_ANNOUNCE_CUSTOM_PREFIX = "announce"
_MAX_AUDIENCE_CHOICES = 25  # Discord caps a command option at 25 static choices.


def _announce_channel(is_site: bool) -> str:
    """The :class:`GuildAnnouncement.DiscordChannel` this audience posts to.

    Derived from the audience (there is no separate channel option): a site-wide
    announcement goes to ``#general-chat``; a guild one goes to the guild's own channel.
    """
    from membership.models import GuildAnnouncement

    channels = GuildAnnouncement.DiscordChannel
    return channels.GENERAL if is_site else channels.GUILD


def _announce_channel_label(is_site: bool, guild: Guild | None) -> str:
    """Human label for the destination channel (used in previews + confirmations)."""
    if is_site:
        return "#general-chat"
    guild = cast("Guild", guild)
    return f"the {guild.name} Discord channel"


def _announce_config_missing_text(is_site: bool, guild: Guild | None) -> str:
    """The "no webhook is set up" copy — we say so plainly rather than silently succeed."""
    if is_site:
        return (
            "There's no #general Discord webhook configured yet, so I can't post a site-wide "
            "announcement. Ask an organizer to set it up in Site Settings."
        )
    guild = cast("Guild", guild)
    return (
        f"{guild.name} doesn't have a Discord channel webhook set up, so I can't post there. "
        "Add one in the guild's settings, or post from the hub."
    )


def _announce_post_blocker(member: Member, *, is_site: bool, guild: Guild | None, mention: str) -> str | None:
    """Return an error message if this member can't post this announcement right now, else ``None``.

    The shared gate for BOTH the slash entry and the confirm click (state can change between the
    two, so the confirm re-checks): authority (``is_fog_admin`` for site, ``can_edit_guild`` for a
    guild), the site+``@role`` mismatch, an empty guild ``discord_role_ids`` for a ``@role`` ping,
    and a missing destination webhook. Any non-``None`` result means "do not post — tell them why".
    """
    from membership.models import resolve_channel_webhook

    if is_site:
        if not member.is_fog_admin:
            return "Only Past Lives admins can post a site-wide announcement from Discord."
        if mention == "role":
            return "The @role ping only works for a guild announcement. Pick a guild, or choose a different ping."
    else:
        guild = cast("Guild", guild)
        if not member.can_edit_guild(guild):
            return (
                f"You're not a lead or staff for {guild.name}, so you can't post its announcements from "
                f"Discord. You can propose one on the hub instead: {hub_url('hub_guild_detail', guild.slug)}"
            )
        if mention == "role" and not guild.discord_role_ids:
            return (
                f"{guild.name} doesn't have a Discord @role set up yet, so I can't ping it. "
                "Ask an organizer to add one in the guild's settings."
            )
    if not resolve_channel_webhook(_announce_channel(is_site), guild):
        return _announce_config_missing_text(is_site, guild)
    return None


def _announce_split_message(message: str) -> tuple[str, str]:
    """Split a single message into a headline (first line, truncated) + the full body."""
    first_line = message.split("\n", 1)[0].strip()
    return truncate(first_line, _ANNOUNCE_TITLE_LIMIT), message


def _announce_mention_literal(mention: str, guild: Guild | None) -> str:
    """The raw Discord ping that rides in the webhook post's ``content`` (``""`` for no ping).

    ``@here`` / ``@everyone`` are their literals; ``role`` expands to every configured role id as
    ``<@&{id}>`` (``build_embed_payload`` turns that into the ``allowed_mentions`` roles gate).
    """
    if mention == "here":
        return "@here"
    if mention == "everyone":
        return "@everyone"
    if mention == "role" and guild is not None:
        return " ".join(f"<@&{role_id}>" for role_id in guild.discord_role_ids)
    return ""


def _announce_ping_display(mention: str) -> str:
    """A ping label for previews / confirmations, wrapped in backticks so it can NEVER ping.

    The preview and confirmation are ephemeral interaction replies; keeping the mention inside
    inline code makes the notification literal inert there — only the real webhook post pings.
    """
    return {
        "here": "`@here` (online members)",
        "everyone": "`@everyone`",
        "role": "the guild's `@role`",
    }[mention]


def _announce_post(*, author: User, is_site: bool, guild: Guild | None, title: str, body: str, mention: str) -> None:
    """Do the actual fan-out: one Discord post + the in-app bell, no email (``send_email=False``).

    Site → :func:`core.events.emit.emit` of ``site_announcement`` routed to the chosen webhook,
    email suppressed. Guild → a published :class:`~membership.models.GuildAnnouncement` (so the
    post also lands on the guild page) whose tested :meth:`notify_members` fan-out carries the
    ping. The mention literal is threaded onto ONLY the Discord message by the emit spine.
    """
    from django.utils import timezone

    from core.events.emit import emit
    from membership.models import GuildAnnouncement, resolve_channel_webhook
    from membership.orientations import _absolute_url

    mention_literal = _announce_mention_literal(mention, guild)
    channel = _announce_channel(is_site)
    if is_site:
        site_url = _absolute_url("/")
        emit(
            "site_announcement",
            actor=author,
            context={
                "member_name": "there",
                "announcement_title": title,
                "announcement_body": body,
                "site_url": site_url,
                "discord_broadcast_webhook": resolve_channel_webhook(channel, None),
            },
            url=site_url,
            period=f"announce:cmd:{timezone.now():%Y%m%d%H%M%S%f}",
            suppress_email=True,
            discord_mention=mention_literal,
        )
        return
    guild = cast("Guild", guild)
    announcement = GuildAnnouncement.objects.create(
        guild=guild,
        author=author,
        title=title,
        body=body,
        send_email=False,
        discord_channel=channel,
    )
    announcement.notify_members(discord_mention=mention_literal)


def _announce_preview_reply(draft: AnnouncementDraft, *, is_site: bool, guild: Guild | None, mention: str) -> dict:
    """The ephemeral Step-2 preview: what will post, to where, pinging whom — Confirm / Cancel."""
    count = draft.recipient_count()
    who = f"about {count} member" + ("" if count == 1 else "s")
    content = (
        "**Ready to post — please confirm.**\n"
        f"> {truncate(draft.title, _ANNOUNCE_PREVIEW_TITLE_LIMIT)}\n\n"
        f"This will post to {_announce_channel_label(is_site, guild)} and ping "
        f"{_announce_ping_display(mention)} — {who} will be notified.\n\n"
        "That's a wide ping. Post it?"
    )
    draft_pk = draft.pk
    row = {
        "type": 1,
        "components": [
            {
                "type": 2,
                "style": 3,
                "label": "Confirm & post",
                "custom_id": f"{_ANNOUNCE_CUSTOM_PREFIX}:confirm:{draft_pk}:{mention}",
            },
            {
                "type": 2,
                "style": 4,
                "label": "Cancel",
                "custom_id": f"{_ANNOUNCE_CUSTOM_PREFIX}:cancel:{draft_pk}:{mention}",
            },
        ],
    }
    return reply(content, ephemeral=True, components=[row])


def _create_announcement(interaction: Interaction, member: Member | None) -> dict:
    """Compose an announcement: validate + authorize, then post now (no ping) or preview + confirm.

    ``requires_link=True`` guarantees ``member`` is non-``None`` (dispatch bounced an unlinked
    caller to the connect prompt). All the cheap guards run before the deferred fan-out. A member
    who can't address the audience is pointed at the hub — nothing is created or posted. Any ping
    routes through the two-step confirm (a persisted draft + Confirm / Cancel buttons); only a
    no-ping post fires immediately.
    """
    from membership.models import AnnouncementDraft, Guild

    member = cast("Member", member)
    message = (option_value(interaction, "message") or "").strip()
    if not message:
        return reply("Add a message to announce — run `/create-announcement` again with some text.", ephemeral=True)

    audience_raw = option_value(interaction, "audience") or ""
    mention = (option_value(interaction, "mention") or "none").strip()
    if mention not in _ANNOUNCE_MENTIONS:
        return reply("I didn't recognize that ping option — pick one from the list and try again.", ephemeral=True)

    is_site = audience_raw == _ANNOUNCE_AUDIENCE_SITE
    guild: Guild | None = None
    if not is_site:
        guild = Guild.objects.filter(slug=audience_raw, is_active=True).first()
        if guild is None:
            return reply(
                "I couldn't find that audience. Run `/create-announcement` again and pick "
                "General or a guild from the list.",
                ephemeral=True,
            )

    if member.user is None:
        return reply(
            "Your Past Lives account isn't fully connected yet — finish linking on the hub, then try again.",
            ephemeral=True,
        )

    blocker = _announce_post_blocker(member, is_site=is_site, guild=guild, mention=mention)
    if blocker is not None:
        return reply(blocker, ephemeral=True)

    title, body = _announce_split_message(message)

    author = cast("User", member.user)  # not-None guarded above
    if mention == "none":
        _announce_post(author=author, is_site=is_site, guild=guild, title=title, body=body, mention="none")
        return reply(f"Posted to {_announce_channel_label(is_site, guild)}. ✅", ephemeral=True)

    # A ping (@here / @everyone / @role) always gets the two-step confirm. Persist the draft so
    # the (potentially long) message survives the round-trip to the button click.
    draft = AnnouncementDraft.objects.create(
        author=author,
        audience=(AnnouncementDraft.Audience.SITE if is_site else AnnouncementDraft.Audience.GUILD),
        guild=guild,
        title=title,
        body=body,
        send_email=False,
        discord_channel=_announce_channel(is_site),
    )
    return _announce_preview_reply(draft, is_site=is_site, guild=guild, mention=mention)


def _create_announcement_component(interaction: Interaction, member: Member | None) -> dict:
    """The Confirm / Cancel click for a pinged announcement — the only place a ping actually posts.

    Parses ``announce:<action>:<draft_pk>:<mention>``, reloads the caller's own unsent draft,
    re-runs the authority + config gate (state can shift between preview and click), then posts.
    The draft is marked sent BEFORE the fan-out so a double-click can't double-post, and the
    preview is replaced in place (type-7 UPDATE_MESSAGE) so its buttons are gone afterward.
    """
    from django.utils import timezone as _tz

    from membership.models import AnnouncementDraft

    member = cast("Member", member)
    custom_id = interaction["data"]["custom_id"]
    parts = custom_id.split(":")
    if (
        len(parts) != 4
        or parts[1] not in ("confirm", "cancel")
        or not parts[2].isdigit()
        or parts[3] not in _ANNOUNCE_PING_MENTIONS
    ):
        logger.warning("Malformed create-announcement custom_id %r", custom_id)
        return error_reply()
    _prefix, action, pk_str, mention = parts

    draft = (
        AnnouncementDraft.objects.filter(pk=int(pk_str), author=member.user, sent_at__isnull=True)
        .select_related("guild")
        .first()
    )
    if draft is None:
        return update_message(
            "This announcement preview has expired or was already posted. "
            "Run `/create-announcement` again if you still want to send it."
        )

    if action == "cancel":
        draft.delete()
        return update_message("Cancelled — nothing was posted.")

    is_site = draft.audience == AnnouncementDraft.Audience.SITE
    guild = draft.guild
    blocker = _announce_post_blocker(member, is_site=is_site, guild=guild, mention=mention)
    if blocker is not None:
        draft.delete()
        return update_message(blocker)

    # Claim the draft atomically: a single conditional UPDATE ... WHERE sent_at IS NULL is the one
    # point that resolves the race. A concurrent confirm (a genuine double-click, or a Discord retry
    # after the ~3s timeout) that loses the claim updates 0 rows and must NOT post. A plain
    # read-then-save here would let two callers both pass the `sent_at IS NULL` read and double-post.
    claimed = AnnouncementDraft.objects.filter(pk=draft.pk, author=member.user, sent_at__isnull=True).update(
        sent_at=_tz.now(), updated_at=_tz.now()
    )
    if not claimed:
        return update_message(
            "This announcement preview has expired or was already posted. "
            "Run `/create-announcement` again if you still want to send it."
        )
    _announce_post(
        author=cast("User", member.user),
        is_site=is_site,
        guild=guild,
        title=draft.title,
        body=draft.body,
        mention=mention,
    )
    return update_message(
        f"Posted to {_announce_channel_label(is_site, guild)} and pinged {_announce_ping_display(mention)}. ✅"
    )


def _announce_message_option() -> dict:
    """The required free-text ``message`` — its first line becomes the announcement headline."""
    return {
        "name": "message",
        "description": "What you want to announce. The first line becomes the headline.",
        "type": 3,
        "required": True,
    }


def _announce_audience_choices() -> dict:
    """The required ``audience`` dropdown: General (site-wide) plus one choice per active guild.

    Built at *serialization* time (inside ``register_discord_commands``), so the DB query is safe.
    Always carries at least the General choice, so it never ships an empty ``choices`` list (which
    would 400 Discord's bulk PUT); capped at Discord's 25-choice limit with any overflow logged.
    """
    from membership.models import Guild

    guilds = list(Guild.objects.filter(is_active=True).order_by("name"))
    choices = [{"name": "Everyone (site-wide)", "value": _ANNOUNCE_AUDIENCE_SITE}]
    choices += [{"name": g.name, "value": g.slug} for g in guilds]
    if len(choices) > _MAX_AUDIENCE_CHOICES:
        dropped = choices[_MAX_AUDIENCE_CHOICES:]
        logger.warning(
            "create-announcement: %d audiences > %d; %r dropped from the picker (still available on the hub).",
            len(choices),
            _MAX_AUDIENCE_CHOICES,
            [c["name"] for c in dropped],
        )
        choices = choices[:_MAX_AUDIENCE_CHOICES]
    return {
        "name": "audience",
        "description": "Who hears it: General (site-wide) or a specific guild.",
        "type": 3,
        "required": True,
        "choices": choices,
    }


def _announce_mention_option() -> dict:
    """The optional ``mention`` ping — defaults to no ping; a ping triggers the confirm step."""
    return {
        "name": "mention",
        "description": "Optional ping (@everyone, @here, or the guild role). Leads and admins only; you'll confirm first.",
        "type": 3,
        "required": False,
        "choices": [
            {"name": "No ping", "value": "none"},
            {"name": "@here (online members)", "value": "here"},
            {"name": "@everyone", "value": "everyone"},
            {"name": "The guild's role", "value": "role"},
        ],
    }


def _create_announcement_options() -> list[dict]:
    """The ``/create-announcement`` options — required message + audience, then the optional ping."""
    return [_announce_message_option(), _announce_audience_choices(), _announce_mention_option()]


CREATE_ANNOUNCEMENT = SlashCommand(
    name="create-announcement",
    description="Post an announcement to Discord and the app (guild leads and admins).",
    handler=_create_announcement,
    options_builder=_create_announcement_options,
    requires_link=True,
    ephemeral=True,
    defer=True,
    scope="guild",
)

register(CREATE_ANNOUNCEMENT)
register_component(
    ComponentHandler(prefix=_ANNOUNCE_CUSTOM_PREFIX, handler=_create_announcement_component, requires_link=True)
)
