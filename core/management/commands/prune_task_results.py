"""Delete finished durable queue task results once a day, with this repo's retention.

The package's own ``prune_db_task_results``, run over every queue: a successful task's row
goes after 30 days, a failed one's after 90, so a failure stays in the admin long after its
alert. Watching the queue is ``check_task_queue``, every 15 minutes.
"""

from __future__ import annotations

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand

SUCCESS_RETENTION_DAYS = 30
FAILED_RETENTION_DAYS = 90


class Command(BaseCommand):
    help = "Delete background task results: successes after 30 days, failures after 90."

    def handle(self, *args: Any, **options: Any) -> None:
        call_command(
            "prune_db_task_results",
            queue_name="*",
            min_age_days=SUCCESS_RETENTION_DAYS,
            failed_min_age_days=FAILED_RETENTION_DAYS,
            stdout=self.stdout,
            stderr=self.stderr,
        )
