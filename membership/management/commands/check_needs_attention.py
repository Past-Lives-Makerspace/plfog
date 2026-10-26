"""Run every active guild against the Needs attention rules (#953).

Registered in ``core/scheduled_jobs.py`` as a DAILY job. A guild that breaks a rule gets its
leads and staff one inbox item each, and one reminder 30 days later while it stays broken; a
guild that is fixed has its items marked read. Re-running the same day sends nothing new
(``membership.needs_attention``).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership.needs_attention import check_all


class Command(BaseCommand):
    help = "Check every active guild against the Needs attention rules and nudge its leads and staff."

    def handle(self, *args: Any, **options: Any) -> None:
        result = check_all()
        self.stdout.write(
            f"{result.guilds} guild(s) checked; {result.open} need attention; "
            f"{result.items} item(s) and {result.reminders} reminder(s) sent; {result.cleared} cleared."
        )
