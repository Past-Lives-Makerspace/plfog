"""Single dispatcher for all scheduled background tasks.

Runs every 15 minutes via a Render cron service. Each registered task is
called in its own try/except so one failure cannot block the rest. Each
task is responsible for its own idempotency.

Time-gated tasks:
- sync_all_sources: runs only when UTC hour == 13 (≈ 6 AM Portland).
"""

from __future__ import annotations

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Dispatch all scheduled background tasks. Safe to run every 15 minutes."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        failed: list[str] = []

        # --- Always-run tasks (idempotent, no-op outside their window) ---
        # ``bill_tabs`` self-gates: it acquires a Postgres advisory lock and exits
        # unless ``BillingSettings.charge_frequency`` says it's billing time
        # (``_is_billing_time``), so running it every tick (no ``--force``) is safe —
        # it no-ops outside the configured schedule and dedupes retries via
        # ``TabCharge.next_retry_at`` + Stripe idempotency keys. Wiring it here
        # (Decision 3) is what finally makes receipts + failed-charge retries run
        # automatically; no ``render.yaml`` change is needed (this dispatcher is the
        # single 15-min cron service).
        for task in (
            "send_voting_reminders",
            "take_cycle_snapshot",
            "send_lease_expiry_reminders",
            "auto_complete_orientations",
            "send_class_reminders",
            "publish_due_events",
            "send_event_reminders",
            "bill_tabs",
            "retry_calendar_pushes",
            "sync_discord_guild_roles",
        ):
            try:
                call_command(task, stdout=self.stdout, stderr=self.stderr)
                self.stdout.write(f"  ✓ {task}")
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  ✗ {task}: {exc}"))
                failed.append(task)

        # --- Daily tasks (~6 AM Portland = 13:xx UTC) ---
        if now.hour == 13:
            for task in ("sync_all_sources", "generate_orientation_slots"):
                try:
                    call_command(task, stdout=self.stdout, stderr=self.stderr)
                    self.stdout.write(f"  ✓ {task}")
                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f"  ✗ {task}: {exc}"))
                    failed.append(task)
        else:
            self.stdout.write("  – daily tasks skipped (not 13:xx UTC)")

        if failed:
            self.stderr.write(self.style.ERROR(f"Failed: {', '.join(failed)}"))
        else:
            self.stdout.write(self.style.SUCCESS("All scheduled tasks completed."))
