"""One-time cleanup (#894): mark read the unread review bell entries whose gate is decided.

Before #894 a decision left the reviewers' "A class needs your review" entries unread, and
an admin's approval closing a lead's gate left the lead's too. Decisions clear them now;
this clears the ones already there (128 in production on 2026-10-23). Safe to run again:
a second run finds nothing. Run as a Render job, then read the count it prints.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from classes.models import ClassApproval


class Command(BaseCommand):
    help = "Mark read the unread class review bell entries whose review gate is already decided."

    def handle(self, *args: Any, **options: Any) -> None:
        marked = ClassApproval.mark_decided_review_bells_read()
        self.stdout.write(self.style.SUCCESS(f"Marked {marked} review bell entr{'y' if marked == 1 else 'ies'} read."))
