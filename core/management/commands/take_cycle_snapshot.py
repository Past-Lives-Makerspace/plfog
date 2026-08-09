"""Auto-take the month-end funding snapshot once per cycle (admin-confirmed results).

Runs on the 15-minute cron (always-run list). Once the calendar rolls into a new
month it captures the cycle that just closed — but it never emails members:
``FundingSnapshot.take`` only logs the snapshot + pings admins to review & send.

Idempotency is layered, modeled on ``send_lease_expiry_reminders``:

1. A once-per-cycle :class:`core.models.EventDelivery` slot
   (``voting.auto_snapshot`` / ``cycle`` / ``system`` / ``voting_close:YYYY-MM``)
   is the authority — the unique constraint makes the first tick of the new month
   claim it and every later (or concurrent) tick a no-op.
2. A window-based duplicate guard — any snapshot taken since the just-closed cycle
   began means an admin already captured it (even with a custom title), so we skip.

Gated on ``VotingSettings.auto_snapshot_enabled``.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import EventDelivery
from membership.models import FundingSnapshot, VotingSettings
from membership.voting import close_period, cycle_start, previous_cycle_label


class Command(BaseCommand):
    help = "Auto-take the month-end funding snapshot once per cycle. Safe to run every 15 minutes."

    def handle(self, *args: Any, **options: Any) -> None:
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
            sent = snapshot.send_results()
            self.stdout.write(self.style.SUCCESS(f"Auto-sent results to {sent} member(s)."))
