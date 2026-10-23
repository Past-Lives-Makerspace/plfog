"""Send the 8 AM Pacific staff digest (#894); safe on every 15 minute tick.

Each person with requests queued before today's 8:00 Pacific gets one email listing the
ones still open, at most once per Pacific date. Before 8:00 it does nothing. The work is
:meth:`core.events.channels.DigestAdapter.flush_due`.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from core.events.scheduler import flush_digests


class Command(BaseCommand):
    help = "Send the 8 AM Pacific staff request digest. Safe to run every 15 minutes."

    def handle(self, *args: Any, **options: Any) -> None:
        sent = flush_digests()
        self.stdout.write(self.style.SUCCESS(f"Sent {len(sent)} staff digest(s)."))
