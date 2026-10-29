"""Pull every hub's vendor Google Sheet into its Vendor Tables (#1013).

Registered in ``core/scheduled_jobs.py`` as a DAILY job (about 6 AM Pacific). Each hub with a sheet link is pulled by
``hq.services.pull_vendor_sheet``, on its own, so one sheet that cannot be read never stops the rest. A failed pull
records its error on the hub (the Vendor Tables page shows it in red) and the run then fails with a
``CommandError`` naming every hub that failed, so ``record_run`` marks it failed and the Webmasters are alerted once
the daily window's retries have not recovered it. A pull that works clears the hub's last error.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from hq.services import pull_every_vendor_sheet


class Command(BaseCommand):
    help = "Pull each hub's vendor Google Sheet into its Vendor Tables."

    def handle(self, *args: Any, **options: Any) -> None:
        failed = pull_every_vendor_sheet()
        if failed:
            reasons = "; ".join(f"{hub.title}: {reason}" for hub, reason in failed)
            raise CommandError(f"Could not pull {len(failed)} vendor sheet(s). {reasons}")
        self.stdout.write("Vendor sheets pulled.")
