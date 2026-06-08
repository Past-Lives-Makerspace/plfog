"""Sync ClassOffering records from the legacy CMS."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from classes.import_service import sync_legacy_cms


class Command(BaseCommand):
    help = "Sync ClassOffering records from the legacy CMS at classes.pastlives.space."

    def handle(self, *args, **options) -> None:
        count = sync_legacy_cms()
        self.stdout.write(self.style.SUCCESS(f"Synced {count} offering(s) from the legacy CMS."))
