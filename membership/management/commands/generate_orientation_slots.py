"""Generate bookable orientation slots from active recurring availability rules."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership import orientations


class Command(BaseCommand):
    help = "Generate orientation slots from active recurring availability rules (idempotent)."

    def handle(self, *args: Any, **options: Any) -> None:
        created = orientations.generate_slots()
        self.stdout.write(f"Created {created} orientation slot(s).")
