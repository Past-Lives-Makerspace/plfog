"""BDD specs for ClassOffering hero crop fields."""

from __future__ import annotations

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from classes.factories import ClassOfferingFactory


def _real_png(size: tuple[int, int] = (1600, 900)) -> SimpleUploadedFile:
    img = Image.new("RGB", size, (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return SimpleUploadedFile("hero.png", buf.getvalue(), content_type="image/png")


def describe_hero_object_position():
    def it_returns_default_when_crop_not_set(db):
        offering = ClassOfferingFactory(image=_real_png())
        assert offering.hero_object_position == "50% 50%"

    def it_computes_center_from_crop_box(db):
        offering = ClassOfferingFactory(image=_real_png((1000, 500)))
        offering.hero_crop_x = 200
        offering.hero_crop_y = 100
        offering.hero_crop_w = 400
        offering.hero_crop_h = 200
        # Center of crop: (400, 200). Source: 1000x500. Percentages: 40%, 40%.
        assert offering.hero_object_position == "40.0% 40.0%"

    def it_falls_back_when_crop_dimensions_missing(db):
        offering = ClassOfferingFactory(image=_real_png())
        offering.hero_crop_x = 10
        offering.hero_crop_y = 10
        # w/h still None.
        assert offering.hero_object_position == "50% 50%"


def describe_hero_crop_reset():
    def it_clears_crop_when_hero_image_changes(db):
        offering = ClassOfferingFactory(image=_real_png((1000, 500)))
        offering.hero_crop_x = 100
        offering.hero_crop_y = 100
        offering.hero_crop_w = 200
        offering.hero_crop_h = 100
        offering.save()

        offering.image = _real_png((1200, 800))
        offering.save()

        offering.refresh_from_db()
        assert offering.hero_crop_x is None
        assert offering.hero_crop_w is None

    def it_keeps_crop_when_hero_image_unchanged(db):
        offering = ClassOfferingFactory(image=_real_png((1000, 500)))
        offering.hero_crop_x = 10
        offering.hero_crop_y = 10
        offering.hero_crop_w = 20
        offering.hero_crop_h = 10
        offering.save()

        offering.title = f"{offering.title} updated"
        offering.save()

        offering.refresh_from_db()
        assert offering.hero_crop_x == 10
        assert offering.hero_crop_w == 20
