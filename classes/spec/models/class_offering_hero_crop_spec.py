"""BDD specs for ClassOffering hero crop fields."""

from __future__ import annotations

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering


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

    def it_supports_direct_percentage_positioning(db):
        offering = ClassOfferingFactory(image=_real_png())
        offering.hero_crop_x = 10
        offering.hero_crop_y = 10
        # w/h still None -> treated as direct percentages.
        assert offering.hero_object_position == "10% 10%"


def describe_scale_hero_crop():
    def _box(offering: ClassOffering) -> tuple[int | None, int | None, int | None, int | None]:
        return offering.hero_crop_x, offering.hero_crop_y, offering.hero_crop_w, offering.hero_crop_h

    def it_shrinks_a_pixel_box_with_its_photo():
        offering = ClassOffering(hero_crop_x=100, hero_crop_y=50, hero_crop_w=2400, hero_crop_h=1350)
        offering.scale_hero_crop(0.5)
        assert _box(offering) == (50, 25, 1200, 675)

    def it_rounds_to_whole_pixels_and_never_below_one():
        offering = ClassOffering(hero_crop_x=3, hero_crop_y=1, hero_crop_w=1, hero_crop_h=1)
        offering.scale_hero_crop(0.4)
        assert _box(offering) == (1, 0, 1, 1)

    def it_leaves_a_focal_point_alone():
        # x and y are percentages when there is no box; scaling them would move the point.
        offering = ClassOffering(hero_crop_x=10, hero_crop_y=10)
        offering.scale_hero_crop(0.5)
        assert _box(offering) == (10, 10, None, None)

    def it_leaves_an_unset_crop_alone():
        offering = ClassOffering()
        offering.scale_hero_crop(0.5)
        assert _box(offering) == (None, None, None, None)

    def it_follows_the_downsize_of_a_fresh_upload_on_save(db, settings):
        # The composer's create mode crops the original file the browser showed; save() then
        # caps the long edge, and the box has to shrink with it or the stored centre points at
        # the wrong part of the stored photo. Here the host framed the left half of 4800x2700:
        # a 16:9 box 2400 wide is 1350 tall, so its centre is a quarter in and a quarter down.
        settings.IMAGE_MAX_LONG_EDGE_HERO = 2400
        offering = ClassOfferingFactory(
            image=_real_png((4800, 2700)), hero_crop_x=0, hero_crop_y=0, hero_crop_w=2400, hero_crop_h=1350
        )
        offering.refresh_from_db()
        assert (offering.image.width, offering.image.height) == (2400, 1350)
        assert _box(offering) == (0, 0, 1200, 675)
        assert offering.hero_object_position == "25.0% 25.0%"


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
