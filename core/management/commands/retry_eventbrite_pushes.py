"""Re-push classes whose Eventbrite listing push is pending or failed (#652).

Wired into ``run_scheduled_tasks`` every ~15 minutes. Self-gating: a no-op when Eventbrite sync
is off, so it is safe on every tick. Each row goes back through
:meth:`ClassOffering.sync_eventbrite_listing`, which lists, updates or ends it as the class now
stands. Bounded per run so a single tick stays cheap.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

_MAX_PER_RUN = 200


class Command(BaseCommand):
    help = "Re-push classes whose Eventbrite listing push is pending or failed."

    def handle(self, *args: Any, **options: Any) -> None:
        from classes.models import ClassOffering
        from core.integrations.eventbrite import EventbriteClient

        if not EventbriteClient.from_settings().enabled:
            self.stdout.write("Eventbrite sync is off, nothing to retry.")
            return
        pushed = 0
        for offering in ClassOffering.objects.needs_eventbrite_push()[:_MAX_PER_RUN]:
            offering.sync_eventbrite_listing()
            pushed += 1
        self.stdout.write(self.style.SUCCESS(f"Retried the Eventbrite push for {pushed} class(es)."))
