"""Download hero images from legacy CMS into Django media storage."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from classes.models import ClassOffering


class Command(BaseCommand):
    help = "Download hero images from legacy_image_url into plfog media storage."

    def handle(self, *args, **options) -> None:
        qs = ClassOffering.objects.filter(legacy_image_url__gt="", image="")
        total = qs.count()
        if not total:
            self.stdout.write("No offerings with pending legacy images.")
            return

        ok = 0
        fail = 0
        for offering in qs:
            try:
                url = offering.legacy_image_url
                # SSRF guard: only allow legacy CMS domain
                if not url.startswith("https://classes.pastlives.space/"):
                    self.stderr.write(f"Skipping untrusted URL: {url}")
                    fail += 1
                    continue

                with urllib.request.urlopen(url, timeout=15) as resp:
                    content = resp.read()

                filename = Path(url.split("?")[0]).name
                offering.image.save(filename, ContentFile(content), save=False)
                offering.legacy_image_url = ""
                offering.save(update_fields=["image", "legacy_image_url"])
                ok += 1
            except Exception as exc:
                self.stderr.write(f"Failed for offering {offering.pk}: {exc}")
                fail += 1

        self.stdout.write(self.style.SUCCESS(f"Downloaded {ok} image(s). {fail} failed."))
