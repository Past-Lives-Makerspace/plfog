"""Auto-complete confirmed orientations whose scheduled time has passed."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership import orientations


class Command(BaseCommand):
    help = "Mark confirmed orientations complete once their slot time has passed (sends thank-you emails)."

    def handle(self, *args: Any, **options: Any) -> None:
        count = orientations.auto_complete()
        self.stdout.write(f"Completed {count} orientation(s).")
