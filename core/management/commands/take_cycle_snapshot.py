"""Auto-take the month-end funding snapshot once per cycle, then make its results draft.

Runs on the 15-minute cron (always-run list), in two phases.

**Phase 1, the snapshot.** Once the calendar rolls into a new month it captures the
cycle that just closed. It sends members nothing: taking the snapshot pings admins
(``voting.results_ready``).

**Phase 2, the results draft.** On every tick, whether or not phase 1 ran, the newest
snapshot gets its "<cycle> Voting Results" draft on the Announcements page, once
(:meth:`membership.models.FundingSnapshot.make_newest_results_draft`). It runs even when
the auto snapshot is switched off or the month's slot is already claimed, because a
snapshot an admin took by hand deserves its draft too; the phase order means the first
tick of a month takes the snapshot and makes its draft in the same run. An admin checks
the draft and sends it through the composer. Sending here would give members the old
results email first and the admin's announcement second. A failure making the draft is
not caught, so the run fails red in Scheduled Jobs.

Idempotency is layered, modeled on ``send_lease_expiry_reminders``:

1. A once-per-cycle :class:`core.models.EventDelivery` slot
   (``voting.auto_snapshot`` / ``cycle`` / ``system`` / ``voting_close:YYYY-MM``)
   is the authority — the unique constraint makes the first tick of the new month
   claim it and every later (or concurrent) tick a no-op.
2. A window-based duplicate guard — any snapshot taken since the just-closed cycle
   began means an admin already captured it (even with a custom title), so we skip.

Phase 1 is gated on ``VotingSettings.auto_snapshot_enabled``; phase 2 is not.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import EventDelivery
from membership.models import FundingSnapshot, VotingSettings
from membership.voting import close_period, cycle_start, previous_cycle_label


class Command(BaseCommand):
    help = (
        "Auto-take the month-end funding snapshot once per cycle, then make the newest snapshot's "
        "results draft once. Safe to run every 15 minutes."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        self._take_snapshot()
        self._make_results_draft()

    def _take_snapshot(self) -> None:
        """Phase 1: take the just-closed cycle's snapshot, once, when the auto snapshot is on."""
        settings = VotingSettings.load()
        if not settings.auto_snapshot_enabled:
            self.stdout.write("Auto-snapshot disabled.")
            return

        now = timezone.now()
        period = close_period(now)
        label = previous_cycle_label(now)

        # Claim the once-per-cycle slot on the EventDelivery ledger (the locked dedupe).
        _row, created = EventDelivery.objects.get_or_create(
            event_key="voting.auto_snapshot",
            target_ref="cycle",
            channel="system",
            period=period,
        )
        if not created:
            self.stdout.write("Auto-snapshot already handled this cycle.")
            return

        # Match on the cycle WINDOW, not the free-text label: any snapshot taken since
        # the just-closed cycle began means an admin already captured it.
        if FundingSnapshot.objects.filter(snapshot_at__gte=cycle_start(now)).exists():
            self.stdout.write("A snapshot already exists for this cycle — skipping auto-take.")
            return

        snapshot = FundingSnapshot.take(title=label, is_auto=True)
        if snapshot is None:
            self.stdout.write("No votes — nothing to snapshot.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Auto-took snapshot '{label}'."))

    def _make_results_draft(self) -> None:
        """Phase 2: make the newest snapshot's results draft, once; runs on every tick."""
        draft = FundingSnapshot.make_newest_results_draft()
        if draft is not None:
            self.stdout.write(self.style.SUCCESS(f"Made the '{draft.title}' draft on the Announcements page."))
