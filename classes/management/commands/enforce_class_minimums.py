"""Warn about and cancel classes under their minimum signups (#1182). Scheduled every ~15 minutes."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from classes.tasks import enforce_class_minimums


class Command(BaseCommand):
    help = "Warn instructors of classes under their minimum signups, and cancel the ones that closed short."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--window-minutes",
            type=int,
            default=15,
            help="Width of the warning window in minutes (default: 15).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        warned, cancelled = enforce_class_minimums(window_minutes=options["window_minutes"])
        self.stdout.write(self.style.SUCCESS(f"Sent {warned} warning(s); cancelled {cancelled} class(es)."))
