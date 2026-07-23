"""Specs for classes/management/commands/optimize_class_images.py."""

from __future__ import annotations

import io
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from classes.factories import ClassOfferingFactory
from classes.models import CLASS_IMAGE_PREFIX, ClassOffering
from core.images import is_content_addressed

pytestmark = pytest.mark.django_db


def _big_jpeg_bytes(size: tuple[int, int] = (4000, 3000)) -> bytes:
    """A JPEG well over the 2400px hero ceiling, with enough detail to be large."""
    img = Image.new("RGB", size)
    pixels = img.load()
    for y in range(0, size[1], 7):
        for x in range(0, size[0], 7):
            pixels[x, y] = ((x * 7) % 256, (y * 13) % 256, (x + y) % 256)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=100)
    return buf.getvalue()


def _store_raw(name: str, content: bytes) -> str:
    """Put raw bytes in storage under a non-content-addressed name, as the migration did."""
    return default_storage.save(name, ContentFile(content))


def describe_optimize_class_images_command():
    def describe_normalization():
        def it_shrinks_an_oversized_image():
            raw = _big_jpeg_bytes()
            stored = _store_raw("classes/images/huge.jpg", raw)
            offering = ClassOfferingFactory(image="")
            ClassOffering.objects.filter(pk=offering.pk).update(image=stored)

            call_command("optimize_class_images", stdout=StringIO(), stderr=StringIO())

            offering.refresh_from_db()
            assert offering.image.name != stored
            assert default_storage.size(offering.image.name) < len(raw)
            with default_storage.open(offering.image.name, "rb") as handle:
                assert max(Image.open(handle).size) <= 2400

        def it_stores_the_result_under_a_content_addressed_key():
            stored = _store_raw("classes/images/named.jpg", _big_jpeg_bytes((600, 400)))
            offering = ClassOfferingFactory(image="")
            ClassOffering.objects.filter(pk=offering.pk).update(image=stored)

            call_command("optimize_class_images", stdout=StringIO(), stderr=StringIO())

            offering.refresh_from_db()
            assert is_content_addressed(offering.image.name, prefix=CLASS_IMAGE_PREFIX)

        def it_deletes_the_object_it_replaced():
            stored = _store_raw("classes/images/replaced.jpg", _big_jpeg_bytes((600, 400)))
            offering = ClassOfferingFactory(image="")
            ClassOffering.objects.filter(pk=offering.pk).update(image=stored)

            call_command("optimize_class_images", stdout=StringIO(), stderr=StringIO())

            assert not default_storage.exists(stored)

    def describe_de_duplication():
        def it_collapses_offerings_that_share_a_picture_onto_one_object():
            raw = _big_jpeg_bytes((900, 600))
            # Two separate stored objects holding identical bytes — exactly what
            # file_overwrite=False produced for the 74 shared legacy images.
            first_name = _store_raw("classes/images/copy_a.jpg", raw)
            second_name = _store_raw("classes/images/copy_b.jpg", raw)
            first = ClassOfferingFactory(image="")
            second = ClassOfferingFactory(image="")
            ClassOffering.objects.filter(pk=first.pk).update(image=first_name)
            ClassOffering.objects.filter(pk=second.pk).update(image=second_name)

            call_command("optimize_class_images", stdout=StringIO(), stderr=StringIO())

            first.refresh_from_db()
            second.refresh_from_db()
            assert first.image.name == second.image.name
            assert default_storage.exists(first.image.name)

        def it_does_not_delete_an_object_a_sibling_still_points_at():
            raw = _big_jpeg_bytes((900, 600))
            shared = _store_raw("classes/images/shared_src.jpg", raw)
            first = ClassOfferingFactory(image="")
            second = ClassOfferingFactory(image="")
            ClassOffering.objects.filter(pk__in=[first.pk, second.pk]).update(image=shared)

            call_command("optimize_class_images", stdout=StringIO(), stderr=StringIO())

            first.refresh_from_db()
            second.refresh_from_db()
            assert first.image.name == second.image.name
            assert default_storage.exists(first.image.name)

    def describe_idempotency():
        def it_skips_images_it_already_optimized_on_a_second_run():
            stored = _store_raw("classes/images/twice.jpg", _big_jpeg_bytes((800, 600)))
            offering = ClassOfferingFactory(image="")
            ClassOffering.objects.filter(pk=offering.pk).update(image=stored)

            call_command("optimize_class_images", stdout=StringIO(), stderr=StringIO())
            offering.refresh_from_db()
            first_pass_name = offering.image.name

            out = StringIO()
            call_command("optimize_class_images", stdout=out, stderr=StringIO())

            offering.refresh_from_db()
            assert offering.image.name == first_pass_name
            assert default_storage.exists(first_pass_name)
            assert "Optimized 0 image(s)" in out.getvalue()
            assert "already optimized" in out.getvalue()

    def describe_dry_run():
        def it_reports_a_saving_without_touching_storage():
            raw = _big_jpeg_bytes()
            stored = _store_raw("classes/images/dry.jpg", raw)
            offering = ClassOfferingFactory(image="")
            ClassOffering.objects.filter(pk=offering.pk).update(image=stored)

            out = StringIO()
            call_command("optimize_class_images", "--dry-run", stdout=out, stderr=StringIO())

            offering.refresh_from_db()
            assert offering.image.name == stored
            assert default_storage.exists(stored)
            assert "Would optimize 1 image(s)" in out.getvalue()
            assert "saved" in out.getvalue()

            default_storage.delete(stored)  # cleanup

    def describe_failures():
        def it_reports_an_unreadable_image_and_exits_non_zero():
            offering = ClassOfferingFactory(image="")
            ClassOffering.objects.filter(pk=offering.pk).update(image="classes/images/gone-missing.jpg")

            err = StringIO()
            with pytest.raises(CommandError, match="failure ceiling"):
                call_command("optimize_class_images", stdout=StringIO(), stderr=err)

            assert "Could not read image" in err.getvalue()

        def it_reports_bytes_that_are_not_an_image():
            stored = _store_raw("classes/images/not-an-image.jpg", b"definitely not a jpeg")
            offering = ClassOfferingFactory(image="")
            ClassOffering.objects.filter(pk=offering.pk).update(image=stored)

            err = StringIO()
            with pytest.raises(CommandError, match="failure ceiling"):
                call_command("optimize_class_images", stdout=StringIO(), stderr=err)

            assert "Could not normalize" in err.getvalue()
            default_storage.delete(stored)  # cleanup

    def it_reports_nothing_to_do_when_no_offering_has_an_image():
        ClassOfferingFactory(image="")

        out = StringIO()
        call_command("optimize_class_images", stdout=out, stderr=StringIO())

        assert "Optimized 0 image(s)" in out.getvalue()
