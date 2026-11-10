"""Watch the durable task queue every 15 minutes: alert on failures, fail when it is stuck.

In order:

1. A task RUNNING longer than ``core.tasks.ABANDONED_AFTER`` (6 hours) lost its worker and
   would stay running for ever, so it is marked FAILED (``abandon_stuck_tasks``).
2. Every task that failed in the last day is alerted through the same once per task per
   Pacific day ledger as the signal alert (``alert_unreported_failures``), so a failure the
   signal never reported (a task whose code no longer imports, or step 1's) still reaches
   the Webmasters, and one it did report stays silent.
3. The command fails, which ``record_run`` turns into the job's own once a day alert, when a
   ready task has waited over an hour since it was due (no worker is running) or a task has
   been running over an hour (a worker may have died; step 1 clears it at 6 hours).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Case, F, When
from django.db.models.functions import Greatest
from django.utils import timezone
from django_tasks_db.models import DBTaskResult, get_date_max

from core.tasks import abandon_stuck_tasks, alert_unreported_failures

STUCK_AFTER = timedelta(hours=1)
FAILURES_LOOK_BACK = timedelta(days=1)


class Command(BaseCommand):
    help = "Alert on failed background tasks; fail when a task has waited or run over an hour."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        abandoned = abandon_stuck_tasks(now)
        if abandoned:
            self.stdout.write(f"Marked {abandoned} abandoned background task(s) failed.")
        alerted = alert_unreported_failures(now - FAILURES_LOOK_BACK)
        if alerted:
            self.stdout.write(f"Checked the failure alert for {', '.join(alerted)}.")

        cutoff = now - STUCK_AFTER
        # Due when enqueued, or at run_after for a delayed task; the package stores "no delay"
        # as a far future run_after, which must not count.
        waiting = (
            DBTaskResult.objects.ready()
            .annotate(
                due_at=Case(
                    When(run_after=get_date_max(), then=F("enqueued_at")),
                    default=Greatest("enqueued_at", "run_after"),
                )
            )
            .filter(due_at__lt=cutoff)
            .count()
        )
        running = DBTaskResult.objects.running().filter(started_at__lt=cutoff).count()
        problems = []
        if waiting:
            problems.append(f"{waiting} background task(s) waited over an hour; is the db_worker service running?")
        if running:
            problems.append(f"{running} background task(s) have run over an hour; a worker may have died mid run.")
        if problems:
            raise CommandError(" ".join(problems))
