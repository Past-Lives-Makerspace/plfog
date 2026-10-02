"""BDD specs for ``hero_aspect_ratio``: the shape the class page banner takes.

The banner shows the whole class photo in a frame of the photo's own shape, so the
composer's crop box is exactly what the page shows. The property never opens a file on a
page view: with a cropped copy the ratio is the stored box's, and an upload's size is read
once and memoised in the cache under its storage name.
"""

from __future__ import annotations

import io
from unittest import mock

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering

LEGACY = "https://classes.pastlives.space/sites/default/files/glen.jpg"
STUB_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _empty_cache():
    cache.clear()
    yield
    cache.clear()


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


def _no_file_reads():
    """Fail the spec if the ratio is read from a file."""
    return mock.patch.object(ClassOffering, "_read_hero_ratio", side_effect=AssertionError("a file was opened"))


def describe_hero_aspect_ratio():
    def describe_with_a_cropped_copy():
        def it_is_the_stored_boxs_shape_and_opens_no_file(db):
            offering = _cropped((100, 50, 400, 225))
            assert offering.hero_cropped
            with _no_file_reads():
                assert offering.hero_aspect_ratio == "400 / 225"
            # The copy is centred in a frame of its own shape, so the position stays put.
            assert offering.hero_object_position == "50% 50%"

        def it_reads_the_box_even_when_the_copys_file_is_gone(db):
            # A file read would give the default; the box answers without one.
            offering = _cropped((100, 50, 400, 225))
            ClassOffering.objects.filter(pk=offering.pk).update(hero_cropped="classes/hero-crops/gone-crop.jpg")
            offering.refresh_from_db()
            assert not default_storage.exists(offering.hero_cropped.name)
            with _no_file_reads():
                assert offering.hero_aspect_ratio == "400 / 225"
            assert cache.get("hero-ratio:classes/hero-crops/gone-crop.jpg") is None

        def it_differs_from_a_copy_clamped_at_the_image_edge_by_the_clamped_pixels(db):
            # render_hero_crop cuts 200 by 200 here; the box says 400 by 225. The page shows a
            # thin backdrop bar for the difference, and never opens the file to learn it.
            offering = _cropped((800, 400, 400, 225))
            with offering.hero_cropped.open("rb") as handle:
                assert Image.open(handle).size == (200, 200)
            with _no_file_reads():
                assert offering.hero_aspect_ratio == "400 / 225"

        def it_falls_through_to_the_copys_file_when_the_box_is_missing(db):
            # Should not happen (the copy follows the box), so the copy's own size stands in.
            offering = _cropped((100, 50, 400, 225))
            ClassOffering.objects.filter(pk=offering.pk).update(hero_crop_w=None, hero_crop_h=None)
            offering.refresh_from_db()
            assert offering.hero_cropped
            assert offering.hero_aspect_ratio == "400 / 225"
            assert cache.get(f"hero-ratio:{offering.hero_cropped.name}") == "400 / 225"

    def describe_with_an_upload_and_no_copy():
        def it_is_the_uploads_shape_read_once_and_then_from_the_cache(db):
            offering = ClassOfferingFactory(image=_real_png((1000, 600)))
            assert not offering.hero_cropped
            key = f"hero-ratio:{offering.image.name}"
            assert cache.get(key) is None
            with mock.patch.object(ClassOffering, "_read_hero_ratio", wraps=ClassOffering._read_hero_ratio) as read:
                assert offering.hero_aspect_ratio == "1000 / 600"
                assert offering.hero_aspect_ratio == "1000 / 600"
            assert read.call_count == 1
            assert cache.get(key) == "1000 / 600"
            # A fresh row for the same file never reads either.
            with _no_file_reads():
                assert ClassOffering.objects.get(pk=offering.pk).hero_aspect_ratio == "1000 / 600"

        def it_is_the_uploads_shape_for_a_focal_point(db):
            # The Adjust tool's shape, width and height 0, is no box: no copy, the whole upload.
            offering = ClassOfferingFactory(
                image=_real_png((800, 500)), hero_crop_x=30, hero_crop_y=70, hero_crop_w=0, hero_crop_h=0
            )
            assert offering.hero_aspect_ratio == "800 / 500"

        def it_is_the_crop_boxs_shape_when_the_file_is_gone(db):
            gone = ClassOfferingFactory(image=_real_png((1000, 600)))
            ClassOffering.objects.filter(pk=gone.pk).update(image="classes/images/gone.jpg")
            gone.refresh_from_db()
            assert gone.hero_aspect_ratio == "16 / 9"

        def it_is_the_crop_boxs_shape_when_the_file_is_not_an_image(db):
            # Present in storage but not an image: Pillow reports no dimensions.
            stub = ClassOfferingFactory(image="")
            stub_name = default_storage.save("classes/images/stub.png", ContentFile(STUB_PNG))
            ClassOffering.objects.filter(pk=stub.pk).update(image=stub_name)
            stub.refresh_from_db()
            assert stub.image
            assert stub.hero_aspect_ratio == "16 / 9"

    def describe_without_an_uploaded_file():
        def it_is_the_crop_boxs_shape(db):
            with _no_file_reads():
                assert ClassOfferingFactory(image="", legacy_image_url=LEGACY).hero_aspect_ratio == "16 / 9"
                assert ClassOfferingFactory(image="").hero_aspect_ratio == "16 / 9"
