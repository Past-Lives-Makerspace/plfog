"""Send the results email for any funding snapshot an admin has queued.

The admin's "Send results" click only records the request (``queue_results_send``);
the actual fan-out happens here, on the scheduler, because emailing the whole
membership takes longer than a web request is allowed to live.

Re-running is safe, and is the recovery path. ``send_results`` keeps the delivery
generation of the request it is fulfilling rather than opening a new one per attempt,
so a run that died partway emails exactly the members the previous attempt missed and
skips everyone who already received it. A snapshot stays queued until a run reaches
everybody or the attempt budget runs out.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership.models import MAX_RESULTS_SEND_ATTEMPTS, FundingSnapshot


class Command(BaseCommand):
    help = "Send results emails for funding snapshots queued by an admin."

    def handle(self, *args: Any, **options: Any) -> None:
        queued = list(
            FundingSnapshot.objects.filter(results_send_requested_at__isnull=False).order_by(
                "results_send_requested_at"
            )
        )
        if not queued:
            self.stdout.write("No queued results sends.")
            return
        for snapshot in queued:
            sent = snapshot.send_results(resend=snapshot.results_send_resend)
            snapshot.refresh_from_db()
            label = f"Sent {sent} results email(s) for '{snapshot.cycle_label}'"
            if snapshot.results_send_requested_at is None:
                self.stdout.write(self.style.SUCCESS(f"{label}."))
                continue
            # Still queued: somebody was claimed but not reached, so this run is not the
            # whole send. Say so here — this text is what lands in the job's run record,
            # and a silent partial send is the failure this whole path exists to prevent.
            self.stdout.write(
                self.style.WARNING(
                    f"{label}, but some members were missed. Retrying next tick "
                    f"(attempt {snapshot.results_send_attempts} of {MAX_RESULTS_SEND_ATTEMPTS})."
                )
            )
