"""Daily (#1260): move each class between Scheduled, Between runs and Retired as time passes.

Publishing, cancelling, archiving and re-dating a run already recompute its class at once;
this catches what only the clock changes, such as a last date passing or a year going by.
It first files any run with no class under one: a run the old release saved while the
release that added classes was deploying.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from classes.models import ClassOffering, ClassRecord


class Command(BaseCommand):
    help = "File runs with no class under one, then recompute every class's state."

    def handle(self, *args: Any, **options: Any) -> None:
        attached = 0
        for run in ClassOffering.objects.filter(class_record__isnull=True).select_related("category"):
            run.save(update_fields=["class_record"])
            attached += 1
        moved = sum(record.refresh_state() for record in ClassRecord.objects.all())
        self.stdout.write(
            self.style.SUCCESS(f"Filed {attached} run(s) under a class; {moved} class(es) changed state.")
        )
