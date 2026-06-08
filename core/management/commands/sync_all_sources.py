"""Sync every external calendar feed and the legacy CMS into the database.

Run daily by a Render cron (see ``render.yaml``). The Community Calendar and the
book/CMS pages read the synced rows straight from the database, so this is the
single scheduled refresh for the whole site.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Sync every calendar feed, the Drupal classes feed, and the legacy CMS into the database."

    def handle(self, *args, **options) -> None:
        from hub.calendar_service import sync_all_sources

        errors = sync_all_sources()
        if errors:
            for message in errors:
                self.stderr.write(self.style.ERROR(message))
            raise CommandError(f"{len(errors)} source(s) failed to sync.")
        self.stdout.write(self.style.SUCCESS("All calendar and class sources synced."))
