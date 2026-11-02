"""Fold finished weeks of page views into the weekly usage table, then prune old raw views (#1078).

Runs on the 15-minute cron (always-run list) and is safe on every tick: a week already folded
is skipped, so only the first tick after a Sunday ends writes anything, and the prune deletes
only rows past :attr:`core.models.PageView.RETENTION_DAYS`. The fold runs first, so a week is
always folded before any of its raw views age out.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import PageView, PageViewWeek


class Command(BaseCommand):
    help = "Fold finished weeks of page views into the weekly usage table, then delete raw views past 90 days."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        written = PageViewWeek.fold_finished_weeks(timezone.localtime(now).date())
        pruned = PageView.prune(now)
        self.stdout.write(
            f"Folded {written} weekly usage row{'s' if written != 1 else ''}; deleted {pruned} old page view{'s' if pruned != 1 else ''}."
        )
