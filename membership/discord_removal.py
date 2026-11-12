"""The ex-member Discord removal pass (#898), run weekly by ``process_discord_removals``.

Each pass walks two lists of :class:`membership.models.DiscordRemoval` rows: queued rows whose
warning day has come, and warned rows whose wait is over. Weekly, that means a member whose
membership ended is warned on the next pass and removed on the one after. Every row is handled in its own transaction under ``select_for_update(skip_locked)``
and re-checked against the status it was picked for, so two overlapping passes never DM or
kick the same person twice; the status change and its action record commit together.

On staging, or anywhere the bot token is blank, the pass returns before it touches a row, so
a copied queue never moves forward without Discord hearing about it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from django.db import transaction
from django.utils import timezone

from core.events.discord_dm import bot_disabled

if TYPE_CHECKING:
    from membership.models import DiscordRemoval, Member

logger = logging.getLogger(__name__)


class RemovalPassError(Exception):
    """Raised after a pass in which a DM or a removal failed, so the run is marked failed."""


@dataclass(frozen=True)
class RemovalPassStats:
    """What one pass did."""

    ran: bool
    handled: int = 0


def process_removals(now: datetime | None = None) -> RemovalPassStats:
    """Send the warning DMs and removals that are due.

    Raises:
        RemovalPassError: When any step failed. The failed steps are already recorded on the
            history page and the rest of the pass has run.
    """
    from core.models import SiteConfiguration
    from membership.models import DiscordRemoval

    if bot_disabled("ex-member removal"):
        return RemovalPassStats(ran=False)
    server_id = SiteConfiguration.load().discord_server_id.strip()
    if not server_id:
        logger.warning("Discord ex-member removal skipped: no Discord server id in Site Settings.")
        return RemovalPassStats(ran=False)

    now = now or timezone.now()
    local = timezone.localtime(now)
    handled = failed = 0
    for pk in DiscordRemoval.objects.due_warnings(local.date()).values_list("pk", flat=True):
        with transaction.atomic():
            row = _claim(pk, DiscordRemoval.Status.QUEUED)
            if row is None:
                continue
            ok = row.send_warning(now)
        handled += 1
        failed += 0 if ok else 1
    for pk in DiscordRemoval.objects.due_kicks(now).values_list("pk", flat=True):
        with transaction.atomic():
            row = _claim(pk, DiscordRemoval.Status.WARNED)
            if row is None:
                continue
            ok = row.remove_from_server(server_id, now)
        handled += 1
        failed += 0 if ok else 1
    if failed:
        raise RemovalPassError(f"{failed} of {handled} Discord removal steps failed; see the history page.")
    return RemovalPassStats(ran=True, handled=handled)


def _claim(pk: int, status: str) -> DiscordRemoval | None:
    """Lock the row if it is still in ``status``; ``None`` when another pass has it or moved it."""
    from membership.models import DiscordRemoval as Removal

    return (
        Removal.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("member")
        .filter(pk=pk, status=status)
        .first()
    )


def put_on_server(member: Member, discord_user_id: str, access_token: str) -> bool:
    """Put whoever just connected Discord on the server, whatever their member status. Never raises.

    Runs in the connect flow right after the link is saved. Adding someone needs their own
    ``guilds.join`` token, which is why nothing else can do it. Discord answers 201 when it added
    them and 204 when they were already there; both count. When the bot removed this member (or
    this Discord account) and that removal is not undone, the removal is marked put back and every
    guild membership they have is pushed back as a role, because the removal took them all;
    ``import_member_guilds`` then pushes the app ones again, which Discord treats as a no-op.
    A blank bot token, a blank server id or a missing token skips quietly; a Discord or network
    error is logged and the connect flow carries on.

    Someone Discord just added (201) also gets every guild membership pushed as a role, Discord
    sourced ones included: a person who left the server on their own lost those roles too.

    Whenever they end up on the server (added, already there, or put back), an active member is
    also given the Member role (``SiteConfiguration.discord_member_role_id``), without which they
    see only #choose-your-guild (feedback request 15). Guests, invited, former and suspended
    members are not, and a blank role id gives nobody it. A refused or failed role call is
    logged by :func:`core.events.discord_roles.assign_role` and the connect carries on.

    There is deliberately no status check (Felix's decision on #1136, feedback request 14):
    anyone who connects Discord is put on the server. A Former member cannot reach the logged in
    Connect (the middleware locks them out of the portal), and the anonymous ``/discord/link/``
    flow is posted inside the server, which a removed member can no longer see.

    Returns:
        True when they are on the server (added now, or already there).
    """
    from core.events.discord_members import JoinOutcome, add_member
    from core.models import SiteConfiguration
    from membership.models import DiscordRemoval as Removal

    if not access_token or bot_disabled("member join"):
        return False
    server_id = SiteConfiguration.load().discord_server_id.strip()
    if not server_id:
        return False
    removal = Removal.objects.awaiting_rejoin(member, discord_user_id)
    if removal is not None:
        if not removal.put_back(server_id, discord_user_id, access_token, timezone.now()):
            return False
        _push_every_role(member)
        _give_member_role(member, server_id, discord_user_id)
        return True
    try:
        outcome = add_member(server_id, discord_user_id, access_token)
    except httpx.HTTPError as exc:
        logger.warning("Discord join for %s failed: %s", discord_user_id, exc)
        return False
    if outcome is JoinOutcome.ADDED:
        _push_every_role(member)
    if outcome not in (JoinOutcome.ADDED, JoinOutcome.ALREADY_THERE):
        return False
    _give_member_role(member, server_id, discord_user_id)
    return True


def _give_member_role(member: Member, server_id: str, discord_user_id: str) -> None:
    """Give an active member the server's Member role. Never raises; a blank role id skips."""
    from core.events.discord_roles import assign_role
    from core.models import SiteConfiguration
    from membership.models import Member as MemberModel

    role_id = SiteConfiguration.load().discord_member_role_id.strip()
    if not role_id or member.status != MemberModel.Status.ACTIVE:
        return
    if not assign_role(server_id, discord_user_id, role_id):
        logger.warning("Discord Member role for %s was not given.", discord_user_id)


def _push_every_role(member: Member) -> None:
    """Push every guild membership the member has, app and Discord sourced, as a role."""
    from core.events.discord_roles import on_membership_changed
    from membership.models import GuildMembership

    for membership in GuildMembership.objects.filter(member=member).select_related("guild"):
        on_membership_changed(membership.guild, member, joined=True)


@dataclass(frozen=True)
class CleanupResult:
    """What the one-time cleanup list did: one line per username, and how many DMs failed."""

    lines: list[str]
    failed: int


def run_cleanup_list(usernames: list[str], ids_by_username: dict[str, str], *, dry_run: bool) -> CleanupResult:
    """Warn and queue each Discord username on Felix's one-time list (#898).

    ``ids_by_username`` is the server's member list (``list_member_ids_by_username``), so only an
    exact username of someone on the server is acted on. A username nobody holds, a Discord id
    an Active member has connected, or one already queued is skipped and recorded. Every other
    one is queued and DMed at once in one transaction; the 15-minute job removes them three days
    later unless they connect to an active member first. ``dry_run`` reports and writes nothing.
    """
    from core.models import AutomationAction
    from membership.models import DISCORD_REMOVAL_TASK_KEY, Member
    from membership.models import DiscordRemoval as Removal

    lines: list[str] = []
    failed = 0
    for username in usernames:
        skip = ""
        user_id = ids_by_username[username] if username in ids_by_username else ""
        if not user_id:
            skip = "nobody on the server has this username"
        elif Member.objects.filter(discord_user_id=user_id, status=Member.Status.ACTIVE).exists():
            skip = "connected to an active member"
        elif Removal.objects.open().filter(discord_user_id=user_id).exists():
            skip = "already queued"
        if skip or dry_run:
            lines.append(f"{username}: {skip or 'would be warned and queued'}")
            if skip and not dry_run:
                AutomationAction.record(
                    DISCORD_REMOVAL_TASK_KEY,
                    kind=AutomationAction.Kind.REMOVAL_QUEUED,
                    result=AutomationAction.Result.SKIPPED,
                    subject=username,
                    detail=f"Not queued: {skip}.",
                )
            continue
        with transaction.atomic():
            removal = Removal.objects.queue_for_cleanup(user_id, username)
            ok = removal.send_warning(timezone.now())
        failed += 0 if ok else 1
        lines.append(
            f"{username}: " + (f"warned, removal due {removal.kick_after:%b %-d %H:%M}" if ok else "DM failed")
        )
    return CleanupResult(lines=lines, failed=failed)
