"""Send the Tour Follow Up emails due this tick (#1345): N hours after each tour ends, per Tour Settings.

Idle while Tour Settings leaves the hours blank. Each tour is claimed before it sends, so a re-run
never sends twice, and only tours whose end plus N hours falls in this 15 minute window are
candidates, so turning the setting on never reaches tours that finished before.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from tours.notify import send_due_follow_ups


class Command(BaseCommand):
    help = "Send the Tour Follow Up emails due this tick (N hours after each tour ends, per Tour Settings)."

    def handle(self, *args: Any, **options: Any) -> None:
        sent = send_due_follow_ups(timezone.now())
        if sent:
            self.stdout.write(self.style.SUCCESS(f"Sent {sent} tour follow up(s)."))
        else:
            self.stdout.write("No tour follow up due this tick.")
