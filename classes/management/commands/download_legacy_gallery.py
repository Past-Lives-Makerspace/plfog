"""Copy legacy class gallery photos from classes.pastlives.space into our own storage.

The catalog sync brings a class's hero across; its gallery (Drupal's ``field_additional_images``)
never came with it, so imported classes showed one photo where the legacy page showed many.
This runs :func:`classes.import_service.sync_legacy_gallery`, which the nightly
``sync_all_sources`` also runs; the command exists for a first backfill and for a re-run by hand.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from classes.import_service import LegacyGalleryImportError, sync_legacy_gallery


class Command(BaseCommand):
    help = "Import each legacy class's gallery photos from classes.pastlives.space into its offering."

    def handle(self, *args, **options) -> None:
        try:
            result = sync_legacy_gallery()
        except LegacyGalleryImportError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(result.summary()))
