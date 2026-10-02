"""BDD specs for ``hero_aspect_ratio``: the shape the class page banner takes.

The banner shows the whole class photo in a frame of the photo's own shape, so the
composer's crop box is exactly what the page shows. The property reads the shown copy's
size the way ``hero_object_position`` does, with the same guards.
"""

from __future__ import annotations

import io

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering

LEGACY = "https://classes.pastlives.space/sites/default/files/glen.jpg"
STUB_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _real_png(size: tuple[int, int]) -> SimpleUploadedFile:
    img = Image.new("RGB", size, (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return SimpleUploadedFile("hero.png", buf.getvalue(), content_type="image/png")


def _cropped(box: tuple[int, int, int, int]) -> ClassOffering:
    x, y, w, h = box
    return ClassOfferingFactory(
        image=_real_png((1000, 600)), hero_crop_x=x, hero_crop_y=y, hero_crop_w=w, hero_crop_h=h
    )


def describe_hero_aspect_ratio():
    def it_is_the_cropped_copys_shape_when_there_is_a_copy(db):
        offering = _cropped((100, 50, 400, 225))
        assert offering.hero_cropped
        assert offering.hero_aspect_ratio == "400 / 225"
        # The copy is centred in a frame of its own shape, so the position stays put.
        assert offering.hero_object_position == "50% 50%"

    def it_is_the_uploads_shape_without_a_copy(db):
        offering = ClassOfferingFactory(image=_real_png((1000, 600)))
        assert not offering.hero_cropped
        assert offering.hero_aspect_ratio == "1000 / 600"

    def it_is_the_uploads_shape_for_a_focal_point(db):
        # The Adjust tool's shape, width and height 0, is no box: no copy, the whole upload.
        offering = ClassOfferingFactory(
            image=_real_png((800, 500)), hero_crop_x=30, hero_crop_y=70, hero_crop_w=0, hero_crop_h=0
        )
        assert offering.hero_aspect_ratio == "800 / 500"

    def it_is_the_crop_boxs_shape_without_an_uploaded_file(db):
        assert ClassOfferingFactory(image="", legacy_image_url=LEGACY).hero_aspect_ratio == "16 / 9"
        assert ClassOfferingFactory(image="").hero_aspect_ratio == "16 / 9"

    def it_is_the_crop_boxs_shape_when_the_file_is_gone(db):
        gone = _cropped((0, 0, 400, 225))
        ClassOffering.objects.filter(pk=gone.pk).update(hero_cropped="classes/hero-crops/gone-crop.jpg")
        gone.refresh_from_db()
        assert gone.hero_cropped
        assert gone.hero_aspect_ratio == "16 / 9"

    def it_is_the_crop_boxs_shape_when_the_file_is_not_an_image(db):
        # Present in storage but not an image: Pillow reports no dimensions.
        stub = ClassOfferingFactory(image="")
        stub_name = default_storage.save("classes/images/stub.png", ContentFile(STUB_PNG))
        ClassOffering.objects.filter(pk=stub.pk).update(image=stub_name)
        stub.refresh_from_db()
        assert stub.image
        assert stub.hero_aspect_ratio == "16 / 9"
