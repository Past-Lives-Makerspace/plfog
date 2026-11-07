"""Retire orientation hours whose Orientor no longer runs that guild's or tool's orientations (#1129)."""

from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand

from membership import orientations

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Retire recurring hours and open future orientation times whose Orientor is no longer an Active "
        "member leading or staffing that guild (or running that tool). Times holding a booking stay."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        sweep = orientations.retire_stale_orientor_hours()
        line = (
            f"Retired {sweep.rules} hours row(s) and {sweep.times} open time(s) from Orientors who no longer "
            f"run them; kept {sweep.kept} booked time(s)."
        )
        logger.info(line)
        self.stdout.write(line)
