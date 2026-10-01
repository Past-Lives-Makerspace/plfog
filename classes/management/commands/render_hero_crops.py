"""Render the cropped hero copy for classes that already carry a crop box.

``ClassOffering.save()`` cuts ``hero_cropped`` from the crop box whenever the box or
the hero image changes (issue #547). Classes cropped before that keep their box and no
copy, so their banner still shows the whole photo. This renders the copy once for each
of them and is safe to run again: a class that has a copy, no box or no uploaded image
is left alone, so a second run renders nothing. Run once against production after the
deploy that added the column.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from classes.models import ClassOffering


class Command(BaseCommand):
    help = "Render the cropped hero copy for every class with a crop box and no copy."

    def handle(self, *args, **options) -> None:
        pending = (
            ClassOffering.objects.filter(hero_crop_w__gt=0, hero_crop_h__gt=0)
            .exclude(image="")
            .filter(hero_cropped="")
            .order_by("pk")
        )
        rendered = 0
        for offering in pending.iterator():
            offering.render_hero_crop()
            if not offering.hero_cropped:
                # The box lies off the image or Pillow could not read the file; the
                # render logged why, and the row is left for the next run.
                continue
            offering.save(update_fields=["hero_cropped"])
            rendered += 1
        self.stdout.write(self.style.SUCCESS(f"Rendered {rendered} hero crops."))
