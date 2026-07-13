"""Promote SCHEDULED community events whose ``publish_at`` has arrived (cron-facing).

Iterates :meth:`membership.models.CommunityEventQuerySet.due_to_publish` and calls
:meth:`membership.models.CommunityEvent.publish_scheduled` per row, each in its own
try/except so one bad Google push cannot stall the batch (``publish()`` already swallows
Google failures into ``FAILED`` — this is belt-and-suspenders). ``publish()`` remains the
idempotent choke point (``announce`` deduped on ``event:{pk}:published``), so re-running
every tick is safe. Added to the 15-minute ``run_scheduled_tasks`` always-run tuple.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Announce + push any SCHEDULED community events whose publish_at has arrived."

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.models import CommunityEvent

        now = timezone.now()
        published = 0
        for event in CommunityEvent.objects.due_to_publish(now):
            try:
                event.publish_scheduled(actor=event.created_by)
                published += 1
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  ✗ publish event {event.pk}: {exc}"))
        if published:
            self.stdout.write(self.style.SUCCESS(f"Published {published} scheduled event(s)."))
        else:
            self.stdout.write("No scheduled event due to publish this tick.")
