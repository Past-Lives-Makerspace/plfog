"""Daily (#1260): move each class between Scheduled, Between runs and Retired as time passes.

Publishing, cancelling, archiving and re-dating a run already recompute its class at once;
this catches what only the clock changes, such as a last date passing or a year going by.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from classes.models import ClassRecord


class Command(BaseCommand):
    help = "Recompute every class's state: Scheduled, Between runs or Retired."

    def handle(self, *args: Any, **options: Any) -> None:
        moved = sum(record.refresh_state() for record in ClassRecord.objects.all())
        self.stdout.write(self.style.SUCCESS(f"{moved} class(es) changed state."))
