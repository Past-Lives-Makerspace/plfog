"""BDD specs for the cropped hero copy (issue #547): the render, the save hooks and the accessor.

The composer's crop box used to change nothing but the banner's centre. Now ``save()`` cuts
``hero_cropped`` from the box and every surface shows that copy through ``hero_image_url``.
"""

from __future__ import annotations

import io
import re

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from classes.factories import ClassOfferingFactory
from classes.models import HERO_CROP_FIELDS, ClassOffering

LEGACY = "https://classes.pastlives.space/sites/default/files/glen.jpg"
COPY_NAME = re.compile(r"^classes/hero-crops/[^/]+-crop(_\w+)?\.(jpg|png)$")


def _real_png(size: tuple[int, int] = (1000, 600)) -> SimpleUploadedFile:
    img = Image.new("RGB", size, (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return SimpleUploadedFile("hero.png", buf.getvalue(), content_type="image/png")


def _encoded(size: tuple[int, int], fmt: str, **save_kwargs) -> bytes:
    img = Image.new("RGB", size, (40, 90, 160))
    buf = io.BytesIO()
    img.save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


def _store_raw(offering: ClassOffering, name: str, content: bytes) -> None:
    """Put bytes in storage under the hero field directly, bypassing the upload hooks.

    ``FieldFile.save()`` commits straight to storage, so ``normalize_field_if_uploaded``
    never runs: the shape the legacy import left, a raw PNG or GIF rather than a JPEG.
    """
    offering.image.save(name, ContentFile(content), save=False)


def _box(offering: ClassOffering, x: int, y: int, w: int, h: int) -> None:
    offering.hero_crop_x, offering.hero_crop_y, offering.hero_crop_w, offering.hero_crop_h = x, y, w, h


def _copy(offering: ClassOffering) -> Image.Image:
    """The stored copy, decoded, with its format intact."""
    with offering.hero_cropped.open("rb") as handle:
        img = Image.open(handle)
        img.load()
        return img


def _cropped(
    size: tuple[int, int] = (1000, 600), box: tuple[int, int, int, int] = (100, 50, 400, 225)
) -> ClassOffering:
    x, y, w, h = box
    return ClassOfferingFactory(image=_real_png(size), hero_crop_x=x, hero_crop_y=y, hero_crop_w=w, hero_crop_h=h)


def describe_render_hero_crop():
    def it_cuts_exactly_the_box_and_keeps_a_jpeg_source_as_jpeg(db):
        offering = _cropped((1000, 600), (100, 50, 400, 225))
        # The PNG upload is normalized to a JPEG on save (core.images), so the source is a JPEG.
        assert offering.image.name.endswith(".jpg")
        assert COPY_NAME.match(offering.hero_cropped.name), offering.hero_cropped.name
        assert ".jpg" in offering.hero_cropped.name
        copy = _copy(offering)
        assert copy.size == (400, 225)
        assert copy.format == "JPEG"

    def it_keeps_a_png_source_as_png(db):
        offering = ClassOfferingFactory(image="")
        _store_raw(offering, "raw.png", _encoded((800, 500), "PNG"))
        _box(offering, 0, 0, 320, 180)
        offering.save()
        copy = _copy(offering)
        assert copy.format == "PNG"
        assert copy.size == (320, 180)
        assert ".png" in offering.hero_cropped.name

    def it_cuts_any_other_format_as_jpeg(db):
        # A GIF is a palette image; the copy is a plain RGB JPEG a browser shows anywhere.
        offering = ClassOfferingFactory(image="")
        _store_raw(offering, "raw.gif", _encoded((800, 500), "GIF"))
        _box(offering, 10, 10, 300, 200)
        offering.save()
        copy = _copy(offering)
        assert copy.format == "JPEG"
        assert copy.mode == "RGB"
        assert copy.size == (300, 200)

    def it_applies_the_exif_orientation_before_cutting(db):
        # A phone photo stored 600 wide by 1000 tall with orientation 6 displays 1000 by 600,
        # and the box is drawn on what the browser shows. Cutting the stored pixels instead
        # would clamp this box to 600 by 600.
        exif = Image.Exif()
        exif[0x0112] = 6
        offering = ClassOfferingFactory(image="")
        _store_raw(offering, "phone.jpg", _encoded((600, 1000), "JPEG", exif=exif.tobytes()))
        _box(offering, 0, 0, 1000, 600)
        offering.save()
        assert _copy(offering).size == (1000, 600)

    def it_clamps_a_box_that_runs_past_the_edge(db):
        # A box measured on a raw original that was later downsized in place can overhang.
        offering = _cropped((1000, 600), (800, 400, 400, 225))
        assert _copy(offering).size == (200, 200)

    def it_clears_the_copy_for_a_box_that_lies_off_the_image(db):
        offering = _cropped()
        first = offering.hero_cropped.name
        _box(offering, 1000, 0, 50, 50)
        offering.save()
        offering.refresh_from_db()
        assert not offering.hero_cropped
        assert not default_storage.exists(first)

    def it_clears_the_copy_when_pillow_cannot_read_the_file(db, caplog):
        offering = ClassOfferingFactory(image="")
        _store_raw(offering, "stub.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        _box(offering, 0, 0, 10, 10)
        offering.save()
        offering.refresh_from_db()
        assert not offering.hero_cropped
        assert "hero crop skipped" in caplog.text

    def it_clears_the_copy_without_an_uploaded_image(db):
        offering = ClassOfferingFactory(
            image="", legacy_image_url=LEGACY, hero_crop_x=0, hero_crop_y=0, hero_crop_w=400, hero_crop_h=225
        )
        assert not offering.hero_cropped
        offering.render_hero_crop()
        assert not offering.hero_cropped

    def it_clears_the_copy_for_a_focal_point(db):
        offering = _cropped()
        _box(offering, 30, 70, 0, 0)
        offering.render_hero_crop()
        assert not offering.hero_cropped


def describe_the_copy_on_save():
    def it_is_cut_when_a_new_class_is_saved_with_a_box(db):
        offering = _cropped()
        offering.refresh_from_db()
        assert COPY_NAME.match(offering.hero_cropped.name)
        assert default_storage.exists(offering.hero_cropped.name)

    def it_is_cut_again_and_the_old_file_deleted_when_the_box_moves(db):
        offering = _cropped((1000, 600), (0, 0, 400, 225))
        first = offering.hero_cropped.name
        _box(offering, 300, 100, 640, 360)
        offering.save()
        offering.refresh_from_db()
        assert offering.hero_cropped.name != first
        assert not default_storage.exists(first)
        assert default_storage.exists(offering.hero_cropped.name)
        assert _copy(offering).size == (640, 360)

    def it_is_dropped_with_its_file_when_the_image_changes(db):
        offering = _cropped()
        first = offering.hero_cropped.name
        offering.image = _real_png((1200, 800))
        offering.save()
        offering.refresh_from_db()
        assert not offering.hero_cropped
        assert not default_storage.exists(first)
        assert offering.hero_crop_w is None

    def it_is_dropped_when_the_box_becomes_a_focal_point(db):
        # The Adjust tool's shape: x and y as percentages, width and height 0, saved with
        # update_fields naming only the four crop columns (hub.views.hub_hero_adjust).
        offering = _cropped()
        first = offering.hero_cropped.name
        _box(offering, 30, 70, 0, 0)
        offering.save(update_fields=list(HERO_CROP_FIELDS))
        offering.refresh_from_db()
        assert not offering.hero_cropped
        assert not default_storage.exists(first)
        assert offering.hero_object_position == "30% 70%"

    def it_is_cut_when_update_fields_names_only_the_box(db):
        offering = ClassOfferingFactory(image=_real_png())
        assert not offering.hero_cropped
        _box(offering, 0, 0, 400, 225)
        offering.save(update_fields=list(HERO_CROP_FIELDS))
        offering.refresh_from_db()
        assert _copy(offering).size == (400, 225)

    def it_is_left_alone_by_an_ordinary_save(db):
        # A render would land under a new, suffixed name because the first file is still
        # there, so an unchanged name proves storage was never touched.
        offering = _cropped()
        first = offering.hero_cropped.name
        offering.title = f"{offering.title} updated"
        offering.save()
        offering.refresh_from_db()
        assert offering.hero_cropped.name == first
        assert default_storage.exists(first)

    def it_is_never_cut_without_a_box(db):
        offering = ClassOfferingFactory(image=_real_png())
        assert not offering.hero_cropped
        offering.title = f"{offering.title} updated"
        offering.save()
        offering.refresh_from_db()
        assert not offering.hero_cropped

    def it_is_never_cut_for_an_imported_photo(db):
        # No uploaded file means no pixels to cut; Adjust is the only tool for that photo.
        offering = ClassOfferingFactory(
            image="", legacy_image_url=LEGACY, hero_crop_x=0, hero_crop_y=0, hero_crop_w=400, hero_crop_h=225
        )
        offering.refresh_from_db()
        assert not offering.hero_cropped
        assert "_legacy-image" in offering.hero_image_url


def describe_hero_object_position():
    def it_centres_on_the_cropped_copy(db):
        # The copy is the box. Its centre on the source ("30.0% 27.1%" here) would drag any
        # frame that is not 16:9 off what the instructor framed; the card follows the banner.
        offering = _cropped((1000, 600), (100, 50, 400, 225))
        assert offering.hero_object_position == "50% 50%"
        assert offering.card_object_position == "50% 50%"

    def it_keeps_a_focal_point(db):
        offering = ClassOfferingFactory(image=_real_png(), hero_crop_x=30, hero_crop_y=70, hero_crop_w=0, hero_crop_h=0)
        assert not offering.hero_cropped
        assert offering.hero_object_position == "30% 70%"

    def it_keeps_the_box_centre_while_there_is_no_copy(db):
        # A class cropped before #547 and not yet backfilled by render_hero_crops still
        # shows the original, positioned on the box's centre.
        offering = _cropped((1000, 600), (100, 50, 400, 225))
        ClassOffering.objects.filter(pk=offering.pk).update(hero_cropped="")
        offering.refresh_from_db()
        assert offering.hero_object_position == "30.0% 27.1%"


def describe_focal_point_on_source():
    def it_maps_a_point_picked_on_the_copy_through_the_box(db):
        offering = _cropped((1000, 600), (0, 0, 400, 225))
        # The copy's centre is the box's centre on the source: 200 of 1000 and 112.5 of 600.
        assert offering.focal_point_on_source(50, 50) == (20, 19)
        assert offering.focal_point_on_source(0, 0) == (0, 0)
        assert offering.focal_point_on_source(100, 100) == (40, 38)

    def it_offsets_by_where_the_box_sits(db):
        offering = _cropped((1000, 600), (300, 100, 640, 360))
        # (300 + 320) of 1000 across, (100 + 180) of 600 down.
        assert offering.focal_point_on_source(50, 50) == (62, 47)

    def it_clamps_to_the_photo(db):
        # A box that overhangs the photo maps its far edge past 100; a point posted off the
        # copy's near edge maps below 0.
        overhanging = _cropped((1000, 600), (800, 400, 400, 225))
        assert overhanging.focal_point_on_source(100, 100) == (100, 100)
        at_the_corner = _cropped((1000, 600), (0, 0, 400, 225))
        assert at_the_corner.focal_point_on_source(-50, -50) == (0, 0)

    def it_is_the_identity_without_a_copy(db):
        focal = ClassOfferingFactory(image=_real_png(), hero_crop_x=30, hero_crop_y=70, hero_crop_w=0, hero_crop_h=0)
        assert focal.focal_point_on_source(50, 50) == (50, 50)
        # A box not yet backfilled shows the original, so a point picked on it needs no mapping.
        not_backfilled = _cropped()
        ClassOffering.objects.filter(pk=not_backfilled.pk).update(hero_cropped="")
        not_backfilled.refresh_from_db()
        assert not_backfilled.focal_point_on_source(50, 50) == (50, 50)

    def it_is_the_identity_when_the_source_cannot_be_read(db):
        gone = _cropped()
        ClassOffering.objects.filter(pk=gone.pk).update(image="classes/images/gone.jpg")
        gone.refresh_from_db()
        assert gone.hero_cropped
        assert gone.focal_point_on_source(50, 50) == (50, 50)
        # Present in storage but not an image: Pillow reports no dimensions.
        stub = _cropped()
        stub_name = default_storage.save("classes/images/stub.png", ContentFile(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64))
        ClassOffering.objects.filter(pk=stub.pk).update(image=stub_name)
        stub.refresh_from_db()
        assert stub.focal_point_on_source(50, 50) == (50, 50)


def describe_hero_image_url():
    def it_prefers_the_cropped_copy(db):
        offering = _cropped()
        assert offering.hero_image_url == offering.hero_cropped.url
        assert "hero-crops/" in offering.hero_image_url
        # The source stays the original, for the cropper.
        assert offering.hero_source_url == offering.image.url

    def it_falls_back_to_the_upload_without_a_copy(db):
        offering = ClassOfferingFactory(image=_real_png())
        assert offering.hero_image_url == offering.image.url
        assert offering.hero_source_url == offering.image.url

    def it_falls_back_to_the_imported_photo_without_an_upload(db):
        offering = ClassOfferingFactory(image="", legacy_image_url=LEGACY)
        assert "_legacy-image" in offering.hero_image_url
        assert offering.hero_source_url == offering.hero_image_url
