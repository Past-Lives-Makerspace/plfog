"""Keep each Tour Guide's connected Google Calendar in step with their tours (#951), every tick.

Reads each connected guide's busy times over the booking horizon (so a visitor is not offered a time
the guide is busy), bells a guide whose calendar now shows them busy during a booked tour, and writes
the tour events waiting to go to Google (booked, moved, reassigned, cancelled), each retried with a
growing wait when Google fails. All of it is :func:`tours.calendar_sync.run`.

Fails, so the Automations page shows it and the Webmasters hear once a day, when a write has failed
four times in a row or a connected calendar has not been read for over an hour: a green run means
the calendars really are current, not only that the command finished.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from tours import calendar_sync, google


class Command(BaseCommand):
    help = "Write facility tours to Tour Guides' Google Calendars and read their busy times."

    def handle(self, *args: Any, **options: Any) -> None:
        if not google.is_configured():
            self.stdout.write("Google Calendar for tour guides is not set up (no OAuth client, or staging).")
            return
        report = calendar_sync.run()
        writes = report.writes
        self.stdout.write(
            f"Busy times read for {report.read} guide(s), {report.read_failed} failed; "
            f"{report.conflicts} booked tour(s) over a busy time; "
            f"events written {writes.done}, failed {writes.failed}, waiting on a reconnect {writes.waiting}, "
            f"given up {writes.given_up}."
        )
        if report.stuck:
            raise CommandError("; ".join(report.stuck) + ".")
