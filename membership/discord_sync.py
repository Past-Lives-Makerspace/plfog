"""Two-way Discord ↔ app guild-membership sync (the service layer; views stay thin).

Three entry points:

* :func:`link_and_import` — orchestration the link views call: resolve the member,
  apply the ordered link guards (spec §5.5), record the link, import their guilds, and
  send the one confirmation email. Returns a :class:`LinkResult` the view maps to a
  landing state.
* :func:`import_member_guilds` — the carrot that runs right after a link: mirror the
  member's Discord reactions into ``source="discord"`` guild memberships, and push their
  existing in-app guilds out to Discord as roles.
* :func:`reconcile_reactions` — the 15-minute cron: keep ``source="discord"`` memberships
  in step with the reaction-role message, adding and (guardedly) removing. Also DMs a
  one-time "you're halfway there" nudge to reactors with no linked member, so a guild
  pick from an unlinked Discord account isn't silently ignored.

The whole thing no-ops when Discord is unconfigured (blank bot token / server / message
ids). Inbound only ever writes ``source="discord"`` rows; outbound only ever writes
Discord roles — the two are disjoint and never fight (spec §5.6). All Discord HTTP goes
through the best-effort readers/writers in :mod:`core.events.discord_reactions` /
:mod:`core.events.discord_roles` (mocked with ``respx`` in tests).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from django.conf import settings
from django.urls import reverse

from core.events import discord_dm, discord_reactions, discord_roles
from core.events.discord_dm import bot_token
from core.events.discord_oauth import DiscordOAuthError, exchange_code, fetch_identity, resolve_member_from_code
from core.events.senders import emit_with_email_shell

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
class ReconcileStats:
    """Counts returned by :func:`reconcile_reactions` for the cron command's stdout."""

    added: int = 0
    removed: int = 0
    skipped_guilds: int = 0
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
    """What :func:`link_and_import` resolved to. Only ``LINKED`` carries import details."""

    outcome: LinkOutcome
    member: Member | None = None
    guilds: list[Guild] = field(default_factory=list)
    complete: bool = True


def _reconcile_config() -> tuple[str, str, str] | None:
    """The ``(server_id, channel_id, message_id)`` for the sync, or ``None`` if disabled.

    Disabled when the bot token or ANY of the three ids is blank (the "no-op when
    unconfigured" idiom) — the whole reaction sync then does nothing.
    """
    from core.models import SiteConfiguration

    if not bot_token():
        return None
    config = SiteConfiguration.load()
    server_id = (config.discord_server_id or "").strip()
    channel_id = (config.discord_role_message_channel_id or "").strip()
    message_id = (config.discord_role_message_id or "").strip()
    if not (server_id and channel_id and message_id):
        return None
    return server_id, channel_id, message_id


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
    from membership.models import DiscordGuildEmoji, GuildMembership

    config = _reconcile_config()
    discord_user_id = (member.discord_user_id or "").strip()
    reacted: dict[int, Guild] = {}
    complete = True
    if config is not None and discord_user_id:
        _server_id, channel_id, message_id = config
        for emoji, guild in DiscordGuildEmoji.objects.mapping().items():
            page = discord_reactions.fetch_reactors(channel_id, message_id, emoji)
            if not page.complete:
                complete = False
            if discord_user_id in page.user_ids:
                reacted[guild.pk] = guild

    for guild in reacted.values():
        GuildMembership.objects.record_discord_join(guild, member)

    # Push the member's in-app guilds onto Discord as roles now that they're linked.
    for membership in GuildMembership.objects.filter(member=member, source=GuildMembership.Source.APP).select_related(
        "guild"
    ):
        discord_roles.on_membership_changed(membership.guild, member, joined=True)

    guilds = sorted(reacted.values(), key=lambda g: g.name)
    return ImportResult(guilds=guilds, complete=complete)


def reconcile_reactions() -> ReconcileStats:
    """Reconcile every ``source="discord"`` membership with the reaction-role message.

    Runs every 15 minutes. No-ops (``ran=False``) unless Discord is fully configured.
    Adds a ``source="discord"`` membership for each linked member who reacted (silent — no
    per-reaction email/notification), and — ONLY on a *complete* fetch for that guild —
    removes the ``source="discord"`` rows for members who no longer react. Never touches
    ``source="app"`` rows in either direction.
    """
    from membership.models import DiscordGuildEmoji, GuildMembership, Member

    config = _reconcile_config()
    if config is None:
        return ReconcileStats(ran=False)
    _server_id, channel_id, message_id = config

    reacted_by_guild: dict[int, set[str]] = {}
    complete_by_guild: dict[int, bool] = {}
    guilds_by_pk: dict[int, Guild] = {}
    for emoji, guild in DiscordGuildEmoji.objects.mapping().items():
        page = discord_reactions.fetch_reactors(channel_id, message_id, emoji)
        guilds_by_pk[guild.pk] = guild
        reacted_by_guild.setdefault(guild.pk, set()).update(page.user_ids)
        complete_by_guild[guild.pk] = complete_by_guild.get(guild.pk, True) and page.complete

    added = 0
    removed = 0
    skipped_guilds = 0
    for pk, ids in reacted_by_guild.items():
        guild = guilds_by_pk[pk]
        # Add: every linked member who reacted becomes a source="discord" member (never
        # downgrading an existing source="app" row).
        if ids:
            for member in Member.objects.filter(discord_user_id__in=ids):
                _membership, created = GuildMembership.objects.record_discord_join(guild, member)
                if created:
                    added += 1
        # Remove (guarded): only on a complete fetch, drop the source="discord" rows for
        # members who no longer react. Inbound never touches source="app".
        if complete_by_guild[pk]:
            deleted, _detail = (
                GuildMembership.objects.filter(guild=guild, source=GuildMembership.Source.DISCORD)
                .exclude(member__discord_user_id__in=ids)
                .delete()
            )
            removed += deleted
        else:
            skipped_guilds += 1
            logger.warning("Discord reconcile skipped removals for guild %s (incomplete fetch).", guild.name)

    # The adds above silently ignore a reactor no member has linked — nudge them (once
    # ever) so their guild pick isn't a black hole. After the membership work so a DM
    # failure can't block the reconcile itself.
    nudged = _nudge_unlinked_reactors({user_id for ids in reacted_by_guild.values() for user_id in ids})

    return ReconcileStats(added=added, removed=removed, skipped_guilds=skipped_guilds, nudged=nudged, ran=True)


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

    Args:
        code: The OAuth authorization code.
        redirect_uri: The callback URL used throughout the flow.
        member: The logged-in member (skips the email match); ``None`` for the anon path.
    """
    from membership.models import Member

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
    result = import_member_guilds(target)
    if result.guilds:
        _send_import_confirmation(target, result)
    return LinkResult(LinkOutcome.LINKED, member=target, guilds=result.guilds, complete=result.complete)


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
            "greeting_name": member.display_name,
            "guilds": guild_links,
            "manage_url": manage_url,
            "complete": result.complete,
        },
        url=manage_url,
        email_to=member.primary_email,
        period=f"discord_import:{member.pk}:{linked_at}",
    )
