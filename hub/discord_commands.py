"""The hub app's member slash commands — ``/link`` (connect) and ``/join-guild`` (join).

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

``/join-guild`` mirrors the in-app :func:`hub.views.guild_join` sequence over Discord: it
records the app-side membership, self-heals the Discord role on every path, and — only for a
brand-new join — posts a public welcome to the guild's channel and fires the join fan-out
(activity + lead notice + optional welcome email). It is ``defer=True`` because that fan-out
can send email; all Discord side-effects are best-effort, so the ``GuildMembership`` row is
the source of truth. It complements (never replaces) the emoji reaction-role flow.

Both mirror the built-in ``/fog-ping`` reference command in :mod:`core.events.discord_commands`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from core.events.discord_commands import SlashCommand, register
from core.events.discord_interactions import reply
from core.events.discord_replies import option_value

if TYPE_CHECKING:
    from core.events.discord_commands import Interaction
    from membership.models import Guild, Member

logger = logging.getLogger(__name__)

# --- Reply copy (spec §6 States 1-3) ------------------------------------------

_NOT_CONFIGURED = (
    "Connecting Discord isn't set up right now. Please try again later, or reach out to a Past Lives organizer."
)
_ALREADY_LINKED = (
    "You're already connected as **{display_name}** — you're all set. Try `/whats-on` to see what's coming up."
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
        return reply(_ALREADY_LINKED.format(display_name=member.display_name), ephemeral=True)

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


# --- /join-guild --------------------------------------------------------------

# Discord caps a command option's static choices at 25.
_MAX_CHOICES = 25


def _guild_choices() -> list[dict]:
    """The ``guild`` option for ``/join-guild`` — one static choice per active guild.

    Built at *serialization* time (inside ``register_discord_commands`` →
    :meth:`SlashCommand.to_api_dict`), never at import time, so it is safe to query the DB.
    Each choice's value is the guild ``slug`` (what the handler resolves on). Resilient at
    both edges:

    * More than 25 active guilds → capped at Discord's limit, with the dropped guilds logged
      (they stay joinable in the hub) rather than silently lost.
    * Zero active guilds → the option ships *without* a ``choices`` key — an empty-choices
      option would 400 Discord's bulk command PUT — leaving a free-text field the handler's
      lenient slug resolution still accepts.
    """
    from membership.models import Guild

    guilds = list(Guild.objects.filter(is_active=True).order_by("name"))
    if len(guilds) > _MAX_CHOICES:
        logger.warning(
            "join-guild: %d active guilds > %d; %r dropped from the picker (still joinable in the hub).",
            len(guilds),
            _MAX_CHOICES,
            [g.name for g in guilds[_MAX_CHOICES:]],
        )
        guilds = guilds[:_MAX_CHOICES]
    option: dict = {"name": "guild", "description": "Which guild to join.", "type": 3, "required": True}
    if guilds:
        option["choices"] = [{"name": g.name, "value": g.slug} for g in guilds]
    return [option]


def _welcome_body(guild: Guild) -> str:
    """The welcome copy — the lead's ``discord_welcome_message`` or a generic fallback."""
    return guild.discord_welcome_message.strip() or f"Welcome to {guild.name}! A lead will say hi soon."


def _join_guild(interaction: Interaction, member: Member | None) -> dict:
    """Join the caller to their chosen guild and welcome them (spec §5).

    Mirrors :func:`hub.views.guild_join`: ``record_app_join`` → ensure the Discord role on
    **every** path (idempotent self-heal) → and, **only for a brand-new join**, post the
    public channel welcome and fire the join fan-out. An ``upgraded`` reaction-join (already
    in the guild/channel) gets neither the public post nor the fan-out — no surprise
    re-welcome. Every Discord side-effect is best-effort; the ``GuildMembership`` row is the
    source of truth, so the ephemeral confirmation always returns.

    ``requires_link=True`` means dispatch resolved a linked member before this runs (an
    unlinked caller gets the connect prompt instead), so ``member`` is non-None.
    """
    from core.events import discord_roles
    from core.events.channels import Message
    from core.events.discord import guild_webhook, post_embed
    from membership import orientations
    from membership.models import Guild, GuildMembership

    member = cast("Member", member)
    slug = option_value(interaction, "guild")
    guild = Guild.objects.filter(slug=slug, is_active=True).first()
    if guild is None:
        return reply("That guild wasn't found. Try `/join-guild` again.", ephemeral=True)

    _membership, created, upgraded = GuildMembership.objects.record_app_join(guild, member)

    # Always ensure the Discord role: idempotent, best-effort, and it self-heals a member who
    # app-joined earlier but whose role never got assigned on a partial run.
    discord_roles.on_membership_changed(guild, member, joined=True)

    if created:
        # Public welcome in the guild's channel — best-effort, gated on discord_post_enabled
        # + a non-blank webhook (guild_webhook returns "" otherwise). No user @-mention: the
        # embed *title* names the member (Message.discord_mention only does @here/@everyone).
        hook = guild_webhook(guild)
        if hook:
            post_embed(
                hook,
                Message(title=f"Welcome {member.display_name} to {guild.name}!", body=_welcome_body(guild)),
            )
        # Welcome fan-out (activity + lead notification + optional welcome email), wrapped so
        # an email hiccup can never swallow the member's confirmation.
        try:
            orientations.member_joined_guild(guild, member)
        except Exception:
            logger.exception("join-guild: welcome fan-out failed for guild=%s member=%s", guild.pk, member.pk)

    if created or upgraded:
        return reply(f"You're in **{guild.name}**! 🎉\n\n{_welcome_body(guild)}", ephemeral=True)
    return reply(f"You're already in **{guild.name}** — nothing to do.", ephemeral=True)


JOIN_GUILD = SlashCommand(
    name="join-guild",
    description="Join a Past Lives guild.",
    handler=_join_guild,
    options_builder=_guild_choices,
    requires_link=True,
    defer=True,
    ephemeral=True,
    scope="guild",
)

register(JOIN_GUILD)
