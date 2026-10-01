"""Specs for classes/management/commands/render_hero_crops.py (issue #547)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from PIL import Image

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db

LEGACY = "https://classes.pastlives.space/sites/default/files/glen.jpg"
BOX = {"hero_crop_x": 0, "hero_crop_y": 0, "hero_crop_w": 400, "hero_crop_h": 225}


def _cropped_before_the_column_existed(stored: str | None = None) -> ClassOffering:
    """A class with a box and no copy: the shape production holds from before #547.

    ``save()`` renders the copy now, so the row is stripped of it afterwards: NULL, the way
    the migration leaves every existing row, or "" the way a cleared copy is written.
    """
    offering = ClassOfferingFactory(image__width=1000, image__height=600, **BOX)
    ClassOffering.objects.filter(pk=offering.pk).update(hero_cropped=stored)
    offering.refresh_from_db()
    assert not offering.hero_cropped
    return offering


def _copy_size(offering: ClassOffering) -> tuple[int, int]:
    with offering.hero_cropped.open("rb") as handle:
        return Image.open(handle).size


def describe_render_hero_crops_command():
    def it_renders_a_copy_for_every_class_with_a_box_and_no_copy():
        pending = _cropped_before_the_column_existed()
        focal_point = ClassOfferingFactory(hero_crop_x=30, hero_crop_y=70, hero_crop_w=0, hero_crop_h=0)
        no_box = ClassOfferingFactory()
        imported = ClassOfferingFactory(image="", legacy_image_url=LEGACY, **BOX)
        done = ClassOfferingFactory(image__width=1000, image__height=600, **BOX)
        done_copy = done.hero_cropped.name

        out = StringIO()
        call_command("render_hero_crops", stdout=out)

        assert "Rendered 1 hero crops." in out.getvalue()
        pending.refresh_from_db()
        assert _copy_size(pending) == (400, 225)
        for untouched in (focal_point, no_box, imported):
            untouched.refresh_from_db()
            assert not untouched.hero_cropped, untouched
        done.refresh_from_db()
        assert done.hero_cropped.name == done_copy

    def it_renders_a_null_row_and_an_empty_row_alike():
        null_row = _cropped_before_the_column_existed(None)
        empty_row = _cropped_before_the_column_existed("")
        assert ClassOffering.objects.filter(pk=null_row.pk, hero_cropped__isnull=True).exists()
        assert ClassOffering.objects.filter(pk=empty_row.pk, hero_cropped="").exists()

        out = StringIO()
        call_command("render_hero_crops", stdout=out)

        assert "Rendered 2 hero crops." in out.getvalue()
        for row in (null_row, empty_row):
            row.refresh_from_db()
            assert _copy_size(row) == (400, 225)

    def it_renders_nothing_on_a_second_run():
        _cropped_before_the_column_existed()
        call_command("render_hero_crops", stdout=StringIO())

        out = StringIO()
        call_command("render_hero_crops", stdout=out)

        assert "Rendered 0 hero crops." in out.getvalue()

    def it_leaves_a_class_whose_file_cannot_be_read_for_the_next_run():
        offering = ClassOfferingFactory(image="")
        offering.image.save("stub.png", ContentFile(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64), save=False)
        for field, value in BOX.items():
            setattr(offering, field, value)
        offering.save()

        out = StringIO()
        call_command("render_hero_crops", stdout=out)

        assert "Rendered 0 hero crops." in out.getvalue()
        offering.refresh_from_db()
        assert not offering.hero_cropped
