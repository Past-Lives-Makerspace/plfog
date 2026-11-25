"""Log "Reservation happened" for confirmed equipment reservations whose end has passed (#1330)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership import equipment


class Command(BaseCommand):
    help = "Log one 'Reservation happened' activity row per confirmed reservation that just ended."

    def handle(self, *args: Any, **options: Any) -> None:
        count = equipment.log_finished_reservations()
        self.stdout.write(f"Logged {count} finished reservation(s).")
