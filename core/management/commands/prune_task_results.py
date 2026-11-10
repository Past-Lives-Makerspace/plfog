"""Tend the durable task queue once a day: prune finished results, then fail if it is stuck.

Pruning is the package's own ``prune_db_task_results``, run over every queue with this
repo's retention: a successful task's row goes after 30 days, a failed one's after 90, so a
failure stays in the admin long after its alert.

Then it checks the outcome the queue exists for, that tasks get run. A ready task still waiting
an hour after it was enqueued means no worker is running (the ``db_worker`` service is down or
was never created); one still running after an hour means a worker died mid run and left
it. Either fails the command, so the dispatcher's ``record_run`` alerts the Webmasters.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

SUCCESS_RETENTION_DAYS = 30
FAILED_RETENTION_DAYS = 90
STUCK_AFTER = timedelta(hours=1)


class Command(BaseCommand):
    help = "Delete old finished background task results, then fail if a task has waited or run over an hour."

    def handle(self, *args: Any, **options: Any) -> None:
        call_command(
            "prune_db_task_results",
            queue_name="*",
            min_age_days=SUCCESS_RETENTION_DAYS,
            failed_min_age_days=FAILED_RETENTION_DAYS,
            stdout=self.stdout,
            stderr=self.stderr,
        )
        cutoff = timezone.now() - STUCK_AFTER
        waiting = DBTaskResult.objects.ready().filter(enqueued_at__lt=cutoff).count()
        running = DBTaskResult.objects.running().filter(started_at__lt=cutoff).count()
        problems = []
        if waiting:
            problems.append(f"{waiting} background task(s) waited over an hour; is the db_worker service running?")
        if running:
            problems.append(f"{running} background task(s) have run over an hour; a worker may have died mid run.")
        if problems:
            raise CommandError(" ".join(problems))
