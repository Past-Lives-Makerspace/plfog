"""Notify members 3 days before the monthly funding vote closes. Daily cron; idempotent."""

from __future__ import annotations

import calendar
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import notifications
from core.models import ScheduledNotificationMarker


class Command(BaseCommand):
    help = "Dispatch the 'voting closing soon' notification when 3 days remain in the cycle."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        last_day = calendar.monthrange(now.year, now.month)[1]
        if now.day != last_day - 3:
            self.stdout.write("Not in the voting-reminder window; nothing to do.")
            return
        key = f"voting_closing:{now:%Y-%m}"
        _, created = ScheduledNotificationMarker.objects.get_or_create(key=key)
        if not created:
            self.stdout.write("Already sent this cycle.")
            return
        notifications.dispatch(
            "voting_closing_soon",
            notifications.active_member_users(),
            title="Guild voting closes soon",
            body="The monthly funding vote closes in 3 days. Cast or update your vote.",
            url="/guilds/voting/",
        )
        self.stdout.write(self.style.SUCCESS("Sent voting-closing reminders."))
