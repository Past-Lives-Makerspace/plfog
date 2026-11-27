"""Send every Tour Follow Up email owed by now (#1345): N hours after each tour ends, per Tour Settings.

Idle while Tour Settings leaves the hours blank. It catches up rather than windowing: every
attended or not marked tour whose end plus N hours has passed and that ended after automatic
sending was turned on, so a missed tick is made up on the next and turning it on never reaches
tours that finished before. Each tour is claimed before it sends, so a re-run never sends twice,
and one that fails is never tried again (a guide sends it by hand).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from tours.notify import send_due_follow_ups


class Command(BaseCommand):
    help = "Send every Tour Follow Up email owed by now (N hours after each tour ends, per Tour Settings)."

    def handle(self, *args: Any, **options: Any) -> None:
        sent = send_due_follow_ups(timezone.now())
        if sent:
            self.stdout.write(self.style.SUCCESS(f"Sent {sent} tour follow up(s)."))
        else:
            self.stdout.write("No tour follow up due this tick.")
