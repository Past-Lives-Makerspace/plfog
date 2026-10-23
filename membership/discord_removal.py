"""The ex-member Discord removal pass (#898), run every 15 minutes by ``process_discord_removals``.

Each pass walks two lists of :class:`membership.models.DiscordRemoval` rows: queued rows whose
warning day has come (from 9 AM Pacific, so nobody is DMed overnight), and warned rows whose
wait is over. Every row is handled in its own transaction under ``select_for_update(skip_locked)``
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

from django.db import transaction
from django.utils import timezone

from core.events.discord_dm import bot_disabled

if TYPE_CHECKING:
    from membership.models import DiscordRemoval, Member

logger = logging.getLogger(__name__)

#: The warning DM waits for the morning instead of going out as the Pacific date turns over.
EARLIEST_DM_HOUR = 9


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
    if local.hour >= EARLIEST_DM_HOUR:
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


def put_back_if_removed(member: Member, discord_user_id: str, access_token: str) -> bool:
    """Put an active member the bot removed back on the server as they connect Discord, with their roles.

    Runs in the connect flow right after the link is saved, and only for an Active member whose
    account (or Discord id) has a removal not yet undone. Re-adding needs the member's own
    ``guilds.join`` token, which is why nothing else can do it. After the re-add every guild
    membership the member has is pushed back as a role, because the removal took them all;
    ``import_member_guilds`` then pushes the app ones again, which Discord treats as a no-op.
    Never raises: the connect flow must finish whatever Discord says.

    Returns:
        True when the member was put back.
    """
    from core.events.discord_roles import on_membership_changed
    from core.models import SiteConfiguration
    from membership.models import DiscordRemoval as Removal
    from membership.models import GuildMembership, Member

    if member.status != Member.Status.ACTIVE or not access_token:
        return False
    removal = Removal.objects.awaiting_rejoin(member, discord_user_id)
    if removal is None or bot_disabled("member re-add"):
        return False
    server_id = SiteConfiguration.load().discord_server_id.strip()
    if not server_id or not removal.put_back(server_id, access_token, timezone.now()):
        return False
    for membership in GuildMembership.objects.filter(member=member).select_related("guild"):
        on_membership_changed(membership.guild, member, joined=True)
    return True


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
