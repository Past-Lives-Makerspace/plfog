"""Two-way Discord ↔ app guild-membership sync (the service layer; views stay thin).

Three entry points:

* :func:`link_and_import` — orchestration the link views call: resolve the member,
  apply the ordered link guards (spec §5.5), record the link, put them on the server, and
  queue :func:`import_discord_guilds`. Returns a :class:`LinkResult` the view maps to a
  landing state.
* :func:`import_discord_guilds` — the durable task queued right after a link: runs
  :func:`import_member_guilds` (mirror the member's Discord reactions into
  ``source="discord"`` guild memberships, and push their existing in-app guilds out to
  Discord as roles) and sends the one confirmation email. It reads every reaction on every
  role message, which ran a connect past gunicorn's 30 seconds inline (feedback request 15).
* :func:`reconcile_reactions` — the 15-minute cron: add a ``source="discord"`` membership for
  each linked reactor, clear the "no Discord role" mark of anyone who reacts again, and give
  every reactor, linked or not, a mapped guild role they lack. Un-reacting never removes a
  membership (Felix, 2026-11-20). Also DMs a one-time "you're halfway there" nudge to reactors
  with no linked member, so a guild pick from an unlinked Discord account isn't silently ignored.
* :func:`sync_roster_roles` — the daily job: give every linked roster member each role of their
  guild they lack. Add only: being off a roster never removes a role, and a membership marked
  ``no_discord_role`` (they un-reacted) is skipped.

The whole thing no-ops when Discord is unconfigured (blank bot token / server / message
ids). Inbound only ever writes ``source="discord"`` rows; outbound only ever writes
Discord roles — the two are disjoint and never fight (spec §5.6). All Discord HTTP goes
through the best-effort readers/writers in :mod:`core.events.discord_reactions` /
:mod:`core.events.discord_roles` (mocked with ``respx`` in tests).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from django.conf import settings
from django.tasks import task
from django.urls import reverse

from core.events import discord_dm, discord_reactions, discord_roles
from core.events.discord_dm import bot_disabled
from core.events.discord_roles import RoleOutcome
from core.events.discord_oauth import DiscordOAuthError, exchange_code, fetch_identity, resolve_member_from_code
from core.events.senders import emit_with_email_shell
from core.tasks import enqueue_on_commit
from membership.discord_removal import put_on_server

if TYPE_CHECKING:
    from membership.models import Guild, Member

logger = logging.getLogger(__name__)


def _absolute_url(path: str) -> str:
    """Turn a relative hub path into an absolute URL using the member-site base."""
    base = settings.MEMBER_BASE_URL.rstrip("/")
    return f"{base}{path}"


@dataclass(frozen=True)
class ImportResult:
    """The outcome of :func:`import_member_guilds`.

    ``guilds`` are the guilds the member's Discord reactions set them up in (name-ordered).
    ``complete`` is ``False`` when any reaction fetch was truncated (a rate-limit /
    partial read) — a partial fetch can only ever *under*-count, so the guilds listed are
    real, but a few more may appear on the next cron tick.
    """

    guilds: list[Guild]
    complete: bool


@dataclass(frozen=True)
class RoleGrants:
    """What a pass of add only role grants did.

    Attributes:
        given: Roles Discord accepted adding.
        failed: Roles whose PUT still failed after its retry or was refused (a 403: the bot's
            role sits below the guild role), plus one when the member list could not be read.
    """

    given: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ReconcileStats:
    """Counts returned by :func:`reconcile_reactions` for the cron command's stdout."""

    added: int = 0
    unmarked: int = 0
    marked: int = 0
    taken: int = 0
    roles: RoleGrants = RoleGrants()
    nudged: int = 0
    ran: bool = True


class LinkOutcome(Enum):
    """The mutually-exclusive outcomes of :func:`link_and_import`, one per landing state."""

    LINKED = "linked"
    NEEDS_LOGIN = "needs_login"
    ALREADY_LINKED_ELSEWHERE = "already_linked_elsewhere"
    ACCOUNT_HAS_OTHER_DISCORD = "account_has_other_discord"
    OAUTH_FAILED = "oauth_failed"


@dataclass(frozen=True)
class LinkResult:
    """What :func:`link_and_import` resolved to. Only ``LINKED`` carries the member."""

    outcome: LinkOutcome
    member: Member | None = None


@dataclass(frozen=True)
class _GuildReactions:
    """Who reacted for each mapped guild, read across every configured role message.

    ``reactors`` is the union, per guild, of the reactors of every emoji mapped to it on
    every message. ``complete`` is ``True`` for a guild only when every one of those
    fetches was complete; :func:`import_member_guilds` reports a truncated read to the member.
    """

    guilds: dict[int, Guild]
    reactors: dict[int, set[str]]
    complete: dict[int, bool]


def _reconcile_config() -> tuple[str, str, list[str]] | None:
    """The ``(server_id, channel_id, message_ids)`` for the sync, or ``None`` if disabled.

    Disabled when the bot token, the server id, the channel id or the main message id is
    blank (the "no-op when unconfigured" idiom); the whole reaction sync then does
    nothing. ``message_ids`` is the main message first, then any extra ones
    (``SiteConfiguration.discord_role_message_ids``).
    """
    from core.models import SiteConfiguration

    if bot_disabled("reaction reconcile"):
        return None
    config = SiteConfiguration.load()
    server_id = (config.discord_server_id or "").strip()
    channel_id = (config.discord_role_message_channel_id or "").strip()
    message_ids = config.discord_role_message_ids
    if not (server_id and channel_id and message_ids):
        return None
    return server_id, channel_id, message_ids


def _read_guild_reactions(channel_id: str, message_ids: list[str]) -> _GuildReactions:
    """Read every mapped emoji's reactors on every role message, folded per guild."""
    from membership.models import DiscordGuildEmoji

    result = _GuildReactions(guilds={}, reactors={}, complete={})
    for emoji, guild in DiscordGuildEmoji.objects.mapping().items():
        result.guilds[guild.pk] = guild
        reactors = result.reactors.setdefault(guild.pk, set())
        for message_id in message_ids:
            page = discord_reactions.fetch_reactors(channel_id, message_id, emoji)
            reactors.update(page.user_ids)
            result.complete[guild.pk] = result.complete.get(guild.pk, True) and page.complete
    return result


def import_member_guilds(member: Member) -> ImportResult:
    """Set the member up in the guilds their Discord reactions point at, and push their
    existing in-app guilds out to Discord as roles.

    1. For each mapped ``(emoji, guild)``, read who reacted; if this member is among them,
       record a ``source="discord"`` membership (never firing the per-join welcome — no
       email storm; an existing ``source="app"`` row is left untouched).
    2. Push every pre-existing ``source="app"`` membership out to Discord as a role.
    3. Return the deduped reacted-guild list + a completeness flag. Completeness is
       ``False`` if any reaction fetch was truncated (safe to add, but a few more guilds
       may surface on the next cron tick).
    """
    from membership.models import GuildMembership

    config = _reconcile_config()
    discord_user_id = (member.discord_user_id or "").strip()
    reacted: dict[int, Guild] = {}
    complete = True
    if config is not None and discord_user_id:
        _server_id, channel_id, message_ids = config
        reactions = _read_guild_reactions(channel_id, message_ids)
        complete = all(reactions.complete.values())
        for pk, ids in reactions.reactors.items():
            if discord_user_id in ids:
                reacted[pk] = reactions.guilds[pk]

    for guild in reacted.values():
        GuildMembership.objects.record_discord_join(guild, member)

    # Push the member's in-app guilds (joined in the app or by an accepted invite) onto Discord as
    # roles now that they're linked.
    for membership in (
        GuildMembership.objects.filter(member=member)
        .exclude(source=GuildMembership.Source.DISCORD)
        .select_related("guild")
    ):
        discord_roles.on_membership_changed(membership.guild, member, joined=True)

    guilds = sorted(reacted.values(), key=lambda g: g.name)
    return ImportResult(guilds=guilds, complete=complete)


def reconcile_reactions() -> ReconcileStats:
    """Mirror every reaction on the role messages into the app and onto Discord. Add only.

    Runs every 15 minutes. No-ops (``ran=False``) unless Discord is fully configured. For each
    mapped guild: a linked reactor gets a ``source="discord"`` membership (silent, no email),
    and their membership's ``no_discord_role`` mark is cleared, since reacting again asks for
    the role back. Then every reactor, linked or not, gets each role of the guild they reacted
    for that they lack, read from one member list: this is the backstop for a reaction the live
    listener missed. Un-reacting never removes a membership: a reaction row whose member no
    longer reacts is marked ``no_discord_role`` and loses the guild's roles (:func:`_mark_unreacted`).
    """
    from membership.models import GuildMembership, Member

    config = _reconcile_config()
    if config is None:
        return ReconcileStats(ran=False)
    server_id, channel_id, message_ids = config

    reactions = _read_guild_reactions(channel_id, message_ids)
    added = 0
    unmarked = 0
    wanted: dict[str, set[str]] = {}
    for pk, ids in reactions.reactors.items():
        guild = reactions.guilds[pk]
        for member in Member.objects.filter(discord_user_id__in=ids):
            _membership, created = GuildMembership.objects.record_discord_join(guild, member)
            added += created
        unmarked += GuildMembership.objects.filter(
            guild=guild, member__discord_user_id__in=ids, no_discord_role=True
        ).update(no_discord_role=False)
        for user_id in ids:
            wanted.setdefault(user_id, set()).update(str(role_id) for role_id in guild.discord_role_ids or [])

    marked, taken = _mark_unreacted(server_id, reactions)
    roles = give_missing_roles(server_id, wanted)
    # The adds above silently ignore a reactor no member has linked — nudge them (once
    # ever) so their guild pick isn't a black hole. After the membership work so a DM
    # failure can't block the reconcile itself.
    nudged = _nudge_unlinked_reactors({user_id for ids in reactions.reactors.values() for user_id in ids})

    return ReconcileStats(
        added=added, unmarked=unmarked, marked=marked, taken=taken, roles=roles, nudged=nudged, ran=True
    )


def _mark_unreacted(server_id: str, reactions: _GuildReactions) -> tuple[int, int]:
    """Mark ``no_discord_role`` on each reaction row whose linked member no longer reacts, and take
    that guild's roles off them. Returns ``(rows marked, roles taken)``.

    Only a ``source="discord"`` row is judged here: it exists because its member reacted, so a
    missing reaction means they took it off. A portal join (``source="app"``) never reacted here,
    so its un-react is the live listener's to see. A guild whose fetch was truncated is skipped,
    since a missing reactor there may only be unread. The membership itself always stays.
    """
    from membership.models import GuildMembership

    marked = 0
    taken = 0
    for pk, ids in reactions.reactors.items():
        if not reactions.complete[pk]:
            continue
        guild = reactions.guilds[pk]
        stale = (
            GuildMembership.objects.filter(guild=guild, source=GuildMembership.Source.DISCORD, no_discord_role=False)
            .exclude(member__discord_user_id="")
            .exclude(member__discord_user_id__in=ids)
        )
        user_ids = sorted(set(stale.values_list("member__discord_user_id", flat=True)))
        if not user_ids:
            continue
        marked += stale.update(no_discord_role=True)
        for user_id in user_ids:
            for role_id in guild.discord_role_ids or []:
                outcome = discord_roles.role_outcome("DELETE", server_id, user_id.strip(), str(role_id), retry=True)
                taken += outcome is RoleOutcome.DONE
    return marked, taken


def give_missing_roles(server_id: str, wanted: dict[str, set[str]]) -> RoleGrants:
    """PUT each role in ``wanted`` (Discord user id to role ids) that its user lacks. Add only; never raises.

    Reads the server member list once; a user not on the server is skipped. A 429 is waited out
    and tried once more, as this is background work.
    """
    wanted = {user_id: role_ids for user_id, role_ids in wanted.items() if role_ids}
    if not wanted:
        return RoleGrants()
    roster = discord_roles.server_member_role_ids(server_id, retry=True)
    if roster is None:
        logger.warning("Discord guild role grants skipped: the server member list could not be read.")
        return RoleGrants(failed=1)
    given = 0
    failed = 0
    for user_id in sorted(set(wanted) & set(roster)):
        for role_id in sorted(wanted[user_id] - roster[user_id]):
            outcome = discord_roles.role_outcome("PUT", server_id, user_id, role_id, retry=True)
            if outcome is RoleOutcome.DONE:
                given += 1
            elif outcome in (RoleOutcome.FAILED, RoleOutcome.REFUSED):
                failed += 1
    return RoleGrants(given=given, failed=failed)


def sync_roster_roles() -> RoleGrants | None:
    """Give every linked roster member each role of their guild they lack (the daily job). Add only.

    A membership marked ``no_discord_role`` is skipped, and a deleted guild's roster too. None
    when the bot token or the server id is blank (nothing was tried).
    """
    from core.models import SiteConfiguration
    from membership.models import GuildMembership

    if bot_disabled("roster role sync"):
        return None
    server_id = SiteConfiguration.load().discord_server_id.strip()
    if not server_id:
        return None
    wanted: dict[str, set[str]] = {}
    rows = (
        GuildMembership.objects.filter(no_discord_role=False, guild__deleted_at__isnull=True)
        .exclude(member__discord_user_id="")
        .values_list("member__discord_user_id", "guild__discord_role_ids")
    )
    for user_id, role_ids in rows:
        wanted.setdefault(user_id.strip(), set()).update(str(role_id) for role_id in role_ids or [])
    return give_missing_roles(server_id, wanted)


def _link_nudge_content() -> str:
    """The one-time "you're halfway there" DM body, with the absolute link URL.

    Points at the same one-click link flow the slash commands send unlinked members to
    (``hub_discord_link_start``). The "your reaction counts automatically" promise is
    real: finishing the link runs :func:`import_member_guilds`, which mirrors the
    standing reaction into a guild membership immediately.
    """
    link_url = _absolute_url(reverse("hub_discord_link_start"))
    return (
        "You're halfway there! 🧩 You picked a guild in #choose-your-guild, but your Discord "
        "isn't linked to your Past Lives account yet — so your pick doesn't count until you finish."
        f"\n\nLink up here: {link_url}"
        "\n\nOnce you're linked, your existing reaction counts automatically — no need to re-react."
    )


def _nudge_unlinked_reactors(reactor_ids: set[str]) -> int:
    """DM the one-time link nudge to role-message reactors who have no linked member.

    Each Discord user is nudged AT MOST ONCE ever — a :class:`DiscordLinkNudge` row is
    the guard, and it is written even when the user's DMs are closed (undeliverable
    forever ≠ retry forever) and kept if they later link and unlink. Any other Discord
    failure raises: no row is written, the cron surfaces the error loudly, and the
    remaining un-nudged ids are retried next tick.
    """
    from membership.models import DiscordLinkNudge, Member

    if not reactor_ids:
        return 0
    linked = set(Member.objects.filter(discord_user_id__in=reactor_ids).values_list("discord_user_id", flat=True))
    already = set(
        DiscordLinkNudge.objects.filter(discord_user_id__in=reactor_ids).values_list("discord_user_id", flat=True)
    )
    nudged = 0
    for discord_user_id in sorted(reactor_ids - linked - already):
        if not discord_dm.send_dm_text(discord_user_id, _link_nudge_content()):
            logger.info("Discord link nudge to %s skipped (DMs closed); marked nudged anyway.", discord_user_id)
        DiscordLinkNudge.objects.create(discord_user_id=discord_user_id)
        nudged += 1
    return nudged


def link_and_import(code: str, redirect_uri: str, *, member: Member | None = None) -> LinkResult:
    """Resolve the target member, apply the link guards in order, link + import.

    The guards (spec §5.5), each mapping to one landing state:

    * OAuth failure → ``OAUTH_FAILED``.
    * No verified account matched (anon path) → ``NEEDS_LOGIN`` (includes a pre-signup
      member with a verified email but no linked User).
    * The Discord account already links a *different* member → ``ALREADY_LINKED_ELSEWHERE``
      (never steal or re-point another member's link).
    * This member already has a *different* Discord → ``ACCOUNT_HAS_OTHER_DISCORD`` (never
      silently swap a connected account). Re-linking the SAME account falls through to
      ``LINKED`` (idempotent re-import).

    A linked member is then put on the Discord server with their own ``guilds.join`` token,
    whatever their status (:func:`membership.discord_removal.put_on_server`). That step never
    fails the link; when it puts them on the server, their open Reconnect Discord nudge (#1239)
    closes as done. The guild import and its email are queued (:func:`import_discord_guilds`),
    because reading every reaction takes longer than a web request may.

    Args:
        code: The OAuth authorization code.
        redirect_uri: The callback URL used throughout the flow.
        member: The logged-in member (skips the email match); ``None`` for the anon path.
    """
    from membership.models import DiscordReconnectNudge, Member

    try:
        if member is not None:
            identity = fetch_identity(exchange_code(code, redirect_uri))
            target: Member | None = member
        else:
            target, identity = resolve_member_from_code(code, redirect_uri)
    except DiscordOAuthError:
        return LinkResult(LinkOutcome.OAUTH_FAILED)

    if target is None:
        return LinkResult(LinkOutcome.NEEDS_LOGIN)

    if Member.objects.filter(discord_user_id=identity.user_id).exclude(pk=target.pk).exists():
        return LinkResult(LinkOutcome.ALREADY_LINKED_ELSEWHERE)

    if target.discord_user_id and target.discord_user_id != identity.user_id:
        return LinkResult(LinkOutcome.ACCOUNT_HAS_OTHER_DISCORD)

    target.link_discord(identity.user_id, handle=identity.handle)
    if put_on_server(target, identity.user_id, identity.access_token):
        DiscordReconnectNudge.objects.close_for(target)
    enqueue_on_commit(import_discord_guilds, target.pk)
    return LinkResult(LinkOutcome.LINKED, member=target)


@task
def import_discord_guilds(member_id: int) -> None:
    """Import a just-linked member's guilds and send the one confirmation email.

    Queued by :func:`link_and_import`. Safe to run twice: the import only adds rows that are
    missing and pushes roles Discord already treats as set, and the email's ``period`` is
    the link's own, so the delivery ledger drops a second send.
    """
    from membership.models import Member

    member = Member.objects.get(pk=member_id)
    result = import_member_guilds(member)
    if result.guilds:
        _send_import_confirmation(member, result)


def _send_import_confirmation(member: Member, result: ImportResult) -> None:
    """Send the single "we set up your N guilds" confirmation (spec §7).

    Transactional (explicit ``email_to``), so it sends regardless of broadcast
    preferences — the member just proved account control via Discord OAuth. EMAIL-only
    (no bell row); the ``period`` is unique per link event so retries dedupe but a genuine
    re-link re-sends.
    """
    guild_links = [
        {"name": guild.name, "url": _absolute_url(reverse("hub_guild_detail", args=[guild.slug]))}
        for guild in result.guilds
    ]
    manage_url = _absolute_url(f"{reverse('hub_user_settings')}?tab=guilds")
    linked_at = member.discord_linked_at.isoformat() if member.discord_linked_at else ""
    emit_with_email_shell(
        "discord_guilds_imported",
        target=member,
        context={"member": member},
        subject="Your Past Lives guilds are set up",
        text_template="membership/emails/discord_guilds_imported.txt",
        html_template="membership/emails/discord_guilds_imported.html",
        template_context={
            "greeting_name": member.greeting_name,
            "guilds": guild_links,
            "manage_url": manage_url,
            "complete": result.complete,
        },
        url=manage_url,
        email_to=member.primary_email,
        period=f"discord_import:{member.pk}:{linked_at}",
    )
