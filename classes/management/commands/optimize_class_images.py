"""Normalize and de-duplicate class hero images already sitting in storage.

The first migration off the legacy CMS wrote raw originals: ``FieldFile.save()``
commits straight to storage, so the ``normalize_field_if_uploaded`` hook on
``ClassOffering.save()`` never fired, and with ``file_overwrite=False`` every row
got its own object even when dozens of classes shared one picture.

This command repairs that in place, on whatever is stored right now:

* each image is re-encoded through ``core.images.normalize_image`` at the hero
  ceiling (EXIF stripped, RGB, JPEG q85), and
* the result is stored under a content-addressed key, so classes sharing a
  picture converge on one object and the copies they replaced are deleted.

It is idempotent and interruptible: a row whose key is already content-addressed
is skipped without a download, and every row is committed as it is processed, so
a re-run resumes where the last one stopped. ``--dry-run`` reports the projected
saving without writing or deleting anything.
"""

from __future__ import annotations

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from PIL import UnidentifiedImageError

from classes.models import CLASS_IMAGE_PREFIX, ClassOffering
from core.files import delete_if_unreferenced
from core.images import (
    content_addressed_name,
    is_content_addressed,
    normalize_image,
    store_content_addressed,
)


# Fraction of processable images that may fail before the command exits non-zero.
FAILURE_ABORT_FRACTION = 0.5


def _mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MB"


class Command(BaseCommand):
    help = "Normalize and de-duplicate stored class hero images."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the projected saving without writing, replacing or deleting anything.",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        qs = ClassOffering.objects.exclude(image="").order_by("pk")

        processed = 0
        skipped = 0
        failed = 0
        before_bytes = 0
        after_bytes = 0
        counted_before: set[str] = set()
        counted_after: set[str] = set()

        for offering in qs.iterator():
            name: str = offering.image.name or ""
            if is_content_addressed(name, prefix=CLASS_IMAGE_PREFIX):
                skipped += 1
                continue

            try:
                with default_storage.open(name, "rb") as handle:
                    raw = handle.read()
            except (FileNotFoundError, OSError, ValueError) as exc:
                self.stderr.write(f"Could not read image for offering {offering.pk} ({name}): {exc}")
                failed += 1
                continue

            if name not in counted_before:
                counted_before.add(name)
                before_bytes += len(raw)

            try:
                data = normalize_image(
                    ContentFile(raw, name=name),
                    max_long_edge=settings.IMAGE_MAX_LONG_EDGE_HERO,
                ).read()
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                self.stderr.write(f"Could not normalize offering {offering.pk} ({name}): {exc}")
                failed += 1
                continue

            target = content_addressed_name(data, prefix=CLASS_IMAGE_PREFIX)
            if target not in counted_after:
                counted_after.add(target)
                after_bytes += len(data)
            processed += 1

            if dry_run:
                continue

            key = store_content_addressed(data, prefix=CLASS_IMAGE_PREFIX)
            # Write the column directly rather than through save(): the hero-crop reset in
            # ClassOffering.save() fires on any image change, and re-pointing a row at the
            # very same picture must not throw away a staff-set crop.
            ClassOffering.objects.filter(pk=offering.pk).update(image=key)
            # The row now points at `key`, so a plain reference check is exactly right:
            # the object we moved off is removed only once nothing at all still uses it —
            # not while a sibling offering shares it, and not when it turned out to be the
            # object we just landed on.
            delete_if_unreferenced(ClassOffering, "image", name)

        saved = max(0, before_bytes - after_bytes)
        pct = (saved / before_bytes * 100) if before_bytes else 0.0
        label = "Would optimize" if dry_run else "Optimized"
        self.stdout.write(
            self.style.SUCCESS(
                f"{label} {processed} image(s) into {len(counted_after)} stored object(s); "
                f"{skipped} already optimized, {failed} failed. "
                f"{_mb(before_bytes)} -> {_mb(after_bytes)} ({_mb(saved)} saved, {pct:.0f}%)."
            )
        )
        if failed and failed >= (processed + failed) * FAILURE_ABORT_FRACTION:
            raise CommandError(
                f"{failed} of {processed + failed} class image(s) could not be optimized — at or "
                f"above the {FAILURE_ABORT_FRACTION:.0%} failure ceiling. Check storage credentials "
                "and connectivity; nothing that failed was modified, so a re-run is safe."
            )
