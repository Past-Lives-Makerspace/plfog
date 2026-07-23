"""Download hero images from the legacy CMS into Django media storage.

Migration step off classes.pastlives.space: every offering that still points at the
Drupal server gets its picture copied into our own storage, normalized to the hero
ceiling, and de-duplicated so a picture shared by many offerings is stored once.
``legacy_image_url`` is cleared as each row lands, which is what stops the site
proxying the old server at request time.

Idempotent: only rows that still have a legacy URL and no local image are touched,
so a re-run after a partial failure picks up exactly what is left.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from PIL import UnidentifiedImageError

from classes.models import CLASS_IMAGE_PREFIX, ClassOffering
from core.images import normalize_image, store_content_addressed


LEGACY_IMAGE_PREFIX = "https://classes.pastlives.space/"

# Fraction of attempted downloads that may fail before the command reports failure via a
# non-zero exit. A handful of dead images is routine; half the catalog failing means the
# host, the network or the configuration is wrong and a human has to look.
FAILURE_ABORT_FRACTION = 0.5


def legacy_image_filename(url: str) -> str:
    """Return the human-readable basename encoded in a legacy image URL.

    Drops the query string and percent-decodes the path, so
    ``.../Closeup%20Hands%202_11.JPG?itok=x`` reads back as
    ``Closeup Hands 2_11.JPG`` rather than ``Closeup20Hands202_11.JPG``.
    """
    return Path(unquote(urlsplit(url).path)).name


class Command(BaseCommand):
    help = "Download hero images from legacy_image_url into plfog media storage."

    def handle(self, *args, **options) -> None:
        qs = ClassOffering.objects.filter(legacy_image_url__gt="", image="").order_by("pk")
        total = qs.count()
        if not total:
            self.stdout.write("No offerings with pending legacy images.")
            return

        ok = 0
        fail = 0
        shared = 0
        seen_keys: set[str] = set()

        for offering in qs.iterator():
            url = offering.legacy_image_url
            # SSRF guard: only allow the legacy CMS domain.
            if not url.startswith(LEGACY_IMAGE_PREFIX):
                self.stderr.write(f"Skipping untrusted URL: {url}")
                fail += 1
                continue

            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    content = resp.read()
            except (OSError, ValueError) as exc:
                self.stderr.write(f"Failed for offering {offering.pk}: {exc}")
                fail += 1
                continue

            filename = legacy_image_filename(url)
            ext = "jpg"
            try:
                data = normalize_image(
                    ContentFile(content, name=filename),
                    max_long_edge=settings.IMAGE_MAX_LONG_EDGE_HERO,
                ).read()
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                # Keep the picture rather than lose it, but store the original bytes
                # under their own extension so nothing claims to be a JPEG.
                self.stderr.write(f"Could not normalize offering {offering.pk} ({filename}): {exc}")
                data = content
                ext = (Path(filename).suffix.lstrip(".").lower() or "bin")[:10]

            key = store_content_addressed(data, prefix=CLASS_IMAGE_PREFIX, ext=ext)
            if key in seen_keys:
                shared += 1
            seen_keys.add(key)

            offering.image = key
            offering.legacy_image_url = ""
            offering.save(update_fields=["image", "legacy_image_url"])
            ok += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Downloaded {ok} image(s) into {len(seen_keys)} stored object(s) "
                f"({shared} re-used an image another class already has). {fail} failed."
            )
        )
        if fail and fail >= total * FAILURE_ABORT_FRACTION:
            raise CommandError(
                f"{fail} of {total} legacy image download(s) failed — at or above the "
                f"{FAILURE_ABORT_FRACTION:.0%} failure ceiling. This usually means the legacy host "
                "is unreachable or storage is misconfigured, not that the images are gone. "
                "Whatever succeeded has been saved; fix the cause and re-run to pick up the rest."
            )
