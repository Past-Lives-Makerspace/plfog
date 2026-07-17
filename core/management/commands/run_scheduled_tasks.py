"""Single dispatcher for all scheduled background tasks.

Runs every 15 minutes via a Render cron service. It iterates the shared job registry
(``core/scheduled_jobs.py``) rather than hard-coded tuples, so the Automations dashboard
in Site Settings and this dispatcher can never disagree about what runs.

Each due job is:
- skipped if it's ``EXTERNAL`` (it has its own Render cron — e.g. ``airtable_pull``),
- skipped if it's ``DAILY`` and it isn't ~6 AM Portland (UTC hour 13),
- skipped if an admin has paused it (``is_enabled`` is false),
- otherwise run inside ``record_run`` (which writes a ScheduledTaskRun row) and its own
  try/except, so one failure cannot block the rest. Each task owns its idempotency.
"""

from __future__ import annotations

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.scheduled_jobs import SCHEDULED_JOBS, Cadence, Trigger, is_enabled, record_run

DAILY_UTC_HOUR = 13  # ~6 AM Portland


class Command(BaseCommand):
    help = "Dispatch all scheduled background tasks. Safe to run every 15 minutes."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        failed: list[str] = []

        # ``bill_tabs`` self-gates: it acquires a Postgres advisory lock and exits unless
        # ``BillingSettings.charge_frequency`` says it's billing time, so running it every
        # tick (no ``--force``) is safe — it no-ops outside the configured schedule.
        for job in SCHEDULED_JOBS:
            if job.cadence == Cadence.EXTERNAL:
                # Runs from its own Render cron, which records around its own work.
                continue
            if job.cadence == Cadence.DAILY and now.hour != DAILY_UTC_HOUR:
                self.stdout.write(f"  – {job.key} skipped (daily, not {DAILY_UTC_HOUR}:xx UTC)")
                continue
            if not is_enabled(job.key):
                self.stdout.write(f"  – {job.key} disabled")
                continue
            try:
                with record_run(job.key, trigger=Trigger.SCHEDULED):
                    call_command(job.command, stdout=self.stdout, stderr=self.stderr)
                self.stdout.write(f"  ✓ {job.key}")
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  ✗ {job.key}: {exc}"))
                failed.append(job.key)

        if failed:
            self.stderr.write(self.style.ERROR(f"Failed: {', '.join(failed)}"))
        else:
            self.stdout.write(self.style.SUCCESS("All scheduled tasks completed."))
