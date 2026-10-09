"""Copy each class's Instagram post picture into storage for the class page's Watch card.

Runs every tick of ``run_scheduled_tasks`` and by hand for a backfill; both do the same
work (:func:`classes.video_thumbnails.refresh_video_thumbnails`). A post that cannot be
fetched is written to stderr and the log and retried a day later.

**The run reports the outcome, not its own steps.** A run that tried at least one post
and fetched none fails (``CommandError``), so ``record_run`` marks it failed and the
Webmasters are alerted: that is what it looks like when Instagram stops answering. Each
failed class has its one day wait written before the raise, so the failing tries, and so
the failed runs, come about once a day per stuck class rather than every tick. A run with
nothing to try, or with at least one picture fetched, ends OK.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from classes.video_thumbnails import refresh_video_thumbnails


class Command(BaseCommand):
    help = "Fetch, refetch or clear the Instagram post picture on every class with an Instagram video link."

    def handle(self, *args: Any, **options: Any) -> None:
        summary = refresh_video_thumbnails()
        for pk, reason in summary.failed:
            self.stderr.write(f"Class {pk}: could not fetch the Instagram picture: {reason}")
        self.stdout.write(
            f"Instagram pictures: {len(summary.fetched)} fetched, {len(summary.failed)} failed, "
            f"{len(summary.cleared)} cleared, {len(summary.waiting)} waiting a day to retry."
        )
        if summary.failed and not summary.fetched:
            reasons = "; ".join(f"class {pk}: {reason}" for pk, reason in summary.failed)
            raise CommandError(
                f"Could not fetch any of {len(summary.failed)} Instagram picture(s) tried; "
                f"each is retried in a day. {reasons}"
            )
