"""Spec helpers for the 8 AM staff digest (#894): run the next morning's send, read what it sent."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core import mail
from django.utils import timezone

from core.events.scheduler import flush_digests

PACIFIC = ZoneInfo("America/Los_Angeles")


def next_8am() -> datetime:
    """Tomorrow at 8:00 Pacific: the first digest after anything queued now."""
    local = timezone.now().astimezone(PACIFIC) + timedelta(days=1)
    return local.replace(hour=8, minute=0, second=0, microsecond=0)


def digest_emails(address: str) -> list:
    """Send the next 8 AM digest, then return every digest in the outbox addressed to ``address``.

    Safe to call more than once in a spec: a second call sends nothing new (once per day).
    """
    flush_digests(next_8am())
    return [m for m in mail.outbox if m.to == [address] and m.subject.startswith("Action Needed:")]
