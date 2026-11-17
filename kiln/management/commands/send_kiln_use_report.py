"""Email the kiln crew last month's Kiln Use Overview (#1228).

Registered in ``core/scheduled_jobs.py`` as a DAILY job, so the dispatcher fires it once a
day at about 6 AM Portland. The command sends only on the 1st, for the month before, so a
mid-month "Run now" sends nothing; ``--force`` sends last month's report on any day. The
send's period is the month, so a re-run delivers nothing twice. The figures are
``kiln.services.kiln_use_report``.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from kiln.services import kiln_use_report, send_kiln_use_report
from membership.wiki_guild import previous_month_window


class Command(BaseCommand):
    help = "Email the Ceramics Guild's lead and staff last month's Kiln Use Overview (on the 1st)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send last month's report even when today is not the 1st. For a shell run; the dashboard never passes it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        today = timezone.localdate()
        if today.day != 1 and not options["force"]:
            self.stdout.write("Not the 1st, so no kiln use report today.")
            return
        month_start, month_end = previous_month_window(today)
        report = kiln_use_report(month_start, month_end)
        if report is None:
            self.stdout.write(
                f"No firings were unloaded in {timezone.localdate(month_start):%B %Y}, so nothing was sent."
            )
            return
        try:
            send_kiln_use_report(report)
        except Exception as exc:
            # A non-zero exit, so the Automations dashboard records a failed run; the job only
            # sends on the 1st, so a quiet failure would lose the month.
            raise CommandError(f"The kiln use report for {report.month_label} failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Sent the kiln use report for {report.month_label}."))
