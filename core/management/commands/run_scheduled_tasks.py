"""Single dispatcher for all scheduled background tasks.

Runs every 15 minutes via a Render cron service. It iterates the shared job registry
(``core/scheduled_jobs.py``) rather than hard-coded tuples, so the Automations dashboard
in Site Settings and this dispatcher can never disagree about what runs.

Each due job is:
- skipped if it's ``EXTERNAL`` (it has its own Render cron — e.g. ``airtable_pull``),
- skipped if it's ``DAILY`` and it isn't ~6 AM Portland (UTC hour 13),
- skipped if it's ``WEEKLY`` and it isn't Monday ~6 AM Portland (UTC weekday 0, hour 13),
- skipped if it's ``DAILY``/``WEEKLY`` and already succeeded this window (the cron ticks ~4x
  inside the qualifying hour, so without this each digest posts ~4 times — the dedup runs it
  once per window while still letting a FAILED run retry in-window),
- skipped if an admin has paused it (``is_enabled`` is false),
- otherwise run inside ``record_run`` (which writes a ScheduledTaskRun row) and its own
  try/except, so one failure cannot block the rest.

A failed ALWAYS run alerts the Webmasters at once. A failed DAILY/WEEKLY run does not, because
a later tick in the window retries it (#933): on each tick of the hour after the window
(``ALERT_UTC_HOUR``), the jobs whose window it was alert if they failed and never recovered.
The alert ledger lets one through per job per Pacific day, so the repeat ticks send nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ScheduledJobState
from core.scheduled_jobs import (
    SCHEDULED_JOBS,
    Cadence,
    Trigger,
    alert_unrecovered_failures,
    has_succeeded_since,
    is_enabled,
    record_run,
)

DAILY_UTC_HOUR = 13  # ~6 AM Portland
WEEKLY_UTC_WEEKDAY = 0  # Monday (WEEKLY jobs run Mondays at DAILY_UTC_HOUR)
ALERT_UTC_HOUR = DAILY_UTC_HOUR + 1  # a DAILY/WEEKLY failure no retry fixed alerts in this hour


class Command(BaseCommand):
    help = "Dispatch all scheduled background tasks. Safe to run every 15 minutes."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        failed: list[str] = []

        # ``bill_tabs`` self-gates: it acquires a Postgres advisory lock and exits unless
        # ``BillingSettings.charge_frequency`` says it's billing time, so running it every
        # tick (no ``--force``) is safe — it no-ops outside the configured schedule.
        chosen = ScheduledJobState.objects.cadences()
        for job in SCHEDULED_JOBS:
            cadence = job.cadence_from(chosen)
            if cadence == Cadence.EXTERNAL:
                # Runs from its own Render cron, which records around its own work.
                continue
            if cadence == Cadence.DAILY and now.hour != DAILY_UTC_HOUR:
                self.stdout.write(f"  – {job.key} skipped (daily, not {DAILY_UTC_HOUR}:xx UTC)")
                continue
            if cadence == Cadence.WEEKLY and (now.weekday() != WEEKLY_UTC_WEEKDAY or now.hour != DAILY_UTC_HOUR):
                self.stdout.write(f"  – {job.key} skipped (weekly, not Monday {DAILY_UTC_HOUR}:xx UTC)")
                continue
            # DAILY/WEEKLY jobs qualify for the whole DAILY_UTC_HOUR, but the cron ticks ~4x within
            # it. Run each at most once per window: skip if it already succeeded since the top of
            # this hour — which, given the hour gate above, is the start of today's / this Monday's
            # window. ALWAYS jobs are exempt (they run every tick); a FAILED run still retries.
            if cadence in (Cadence.DAILY, Cadence.WEEKLY) and has_succeeded_since(
                job.key, now.replace(minute=0, second=0, microsecond=0)
            ):
                self.stdout.write(f"  – {job.key} skipped (already ran this window)")
                continue
            if not is_enabled(job.key):
                self.stdout.write(f"  – {job.key} disabled")
                continue
            try:
                with record_run(
                    job.key, trigger=Trigger.SCHEDULED, defer_alert=cadence in (Cadence.DAILY, Cadence.WEEKLY)
                ):
                    call_command(job.command, stdout=self.stdout, stderr=self.stderr)
                self.stdout.write(f"  ✓ {job.key}")
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  ✗ {job.key}: {exc}"))
                failed.append(job.key)

        if now.hour == ALERT_UTC_HOUR:
            self._alert_after_window(now, chosen)

        if failed:
            self.stderr.write(self.style.ERROR(f"Failed: {', '.join(failed)}"))
        else:
            self.stdout.write(self.style.SUCCESS("All scheduled tasks completed."))

    def _alert_after_window(self, now: datetime, chosen: dict[str, str]) -> None:
        """Alert for each job whose window was the hour before and that failed in it without recovering."""
        window_keys = [
            job.key
            for job in SCHEDULED_JOBS
            if job.cadence_from(chosen) == Cadence.DAILY
            or (job.cadence_from(chosen) == Cadence.WEEKLY and now.weekday() == WEEKLY_UTC_WEEKDAY)
        ]
        window_start = now.replace(hour=DAILY_UTC_HOUR, minute=0, second=0, microsecond=0)
        for key in alert_unrecovered_failures(window_keys, window_start, window_start + timedelta(hours=1)):
            self.stderr.write(self.style.ERROR(f"  ! {key} alerted (failed in its window and never recovered)"))
