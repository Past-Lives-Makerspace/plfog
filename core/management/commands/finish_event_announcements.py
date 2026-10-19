"""Finish event announcements an interrupted process left unsent (cron-facing, #845).

Publishing an event stamps ``CommunityEvent.announcement_owed_since`` and sends the member
announcement on a background thread after the response. A redeploy or a kill can end that
thread mid send; the stamp stays, and this job, run every 15 minutes by
``run_scheduled_tasks``, picks up every event still owed after
``CommunityEvent.ANNOUNCEMENT_FINISH_GRACE`` and finishes it. The delivery ledger claims
each (member, channel) slot once under the event's ``event:{pk}:published`` period, so the
members and the Discord post the thread already reached are skipped and nobody hears twice.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Finish any event announcement an interrupted process left unsent."

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.models import CommunityEvent

        finished = 0
        failed: list[int] = []
        for event in CommunityEvent.objects.announcement_overdue(timezone.now()):
            try:
                if event.finish_announcement():
                    finished += 1
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  ✗ finish announcement for event {event.pk}: {exc}"))
                failed.append(event.pk)
        if finished:
            self.stdout.write(self.style.SUCCESS(f"Finished {finished} event announcement(s)."))
        else:
            self.stdout.write("No event announcement left unfinished.")
        if failed:
            # Raise so record_run marks the run failed and the Webmaster hears; the stamp
            # stays on each failed event, so the next tick tries it again.
            raise RuntimeError(f"Could not finish the announcement for event(s) {failed}.")
