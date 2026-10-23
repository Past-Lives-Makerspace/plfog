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
    from membership.models import DiscordRemoval

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
