"""BDD specs for ClassImage and ClassOffering.display_images."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from classes.factories import CategoryFactory, ClassImageFactory, ClassOfferingFactory
from classes.models import MAX_GALLERY_IMAGES, ClassImage


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    # Minimal PNG: an 8-byte signature is enough for Django's ImageField when
    # PIL isn't validating (tests run with validators that only check size).
    buf = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def describe_ClassImage():
    def it_orders_by_sort_then_created(db):
        offering = ClassOfferingFactory()
        a = ClassImageFactory(class_offering=offering, image=_image_file("a.png"), sort_order=2)
        b = ClassImageFactory(class_offering=offering, image=_image_file("b.png"), sort_order=1)
        ordered = list(offering.gallery_images.all())
        assert ordered == [b, a]

    def it_cascades_when_offering_is_deleted(db):
        offering = ClassOfferingFactory()
        ClassImageFactory(class_offering=offering, image=_image_file())
        ClassImageFactory(class_offering=offering, image=_image_file("b.png"))
        offering_pk = offering.pk
        offering.delete()

        assert ClassImage.objects.filter(class_offering_id=offering_pk).count() == 0

    def describe_clean():
        def it_rejects_the_11th_image(db):
            offering = ClassOfferingFactory()
            for i in range(MAX_GALLERY_IMAGES):
                ClassImageFactory(class_offering=offering, image=_image_file(f"{i}.png"), sort_order=i)
            eleventh = ClassImage(class_offering=offering, image=_image_file("x.png"))
            with pytest.raises(ValidationError):
                eleventh.full_clean()

        def it_allows_the_10th_image(db):
            offering = ClassOfferingFactory()
            for i in range(MAX_GALLERY_IMAGES - 1):
                ClassImageFactory(class_offering=offering, image=_image_file(f"{i}.png"), sort_order=i)
            tenth = ClassImage(class_offering=offering, image=_image_file("ten.png"))
            tenth.full_clean()  # must not raise

        def it_allows_resaving_an_existing_image_at_the_cap(db):
            offering = ClassOfferingFactory()
            images = [
                ClassImageFactory(class_offering=offering, image=_image_file(f"{i}.png"), sort_order=i)
                for i in range(MAX_GALLERY_IMAGES)
            ]
            images[0].alt_text = "updated"
            images[0].full_clean()  # excludes self.pk — must not raise


def describe_add_gallery_images():
    def it_creates_rows_for_each_file(db):
        offering = ClassOfferingFactory()
        offering.add_gallery_images([_image_file("a.png"), _image_file("b.png")])
        assert offering.gallery_images.count() == 2

    def it_rejects_a_batch_that_exceeds_the_cap(db):
        offering = ClassOfferingFactory()
        files = [_image_file(f"{i}.png") for i in range(MAX_GALLERY_IMAGES + 1)]
        with pytest.raises(ValidationError):
            offering.add_gallery_images(files)
        assert offering.gallery_images.count() == 0  # atomic: nothing created

    def it_rejects_when_existing_plus_batch_exceeds_cap(db):
        offering = ClassOfferingFactory()
        for i in range(MAX_GALLERY_IMAGES - 1):
            ClassImageFactory(class_offering=offering, image=_image_file(f"e{i}.png"), sort_order=i)
        with pytest.raises(ValidationError):
            offering.add_gallery_images([_image_file("x.png"), _image_file("y.png")])
        assert offering.gallery_images.count() == MAX_GALLERY_IMAGES - 1  # batch rejected whole

    def it_appends_after_existing_images(db):
        offering = ClassOfferingFactory()
        ClassImageFactory(class_offering=offering, image=_image_file("first.png"), sort_order=0)
        offering.add_gallery_images([_image_file("second.png")])
        orders = list(offering.gallery_images.order_by("sort_order").values_list("sort_order", flat=True))
        assert orders == [0, 1]  # appended, not colliding at 0


def describe_display_images():
    def it_returns_hero_then_gallery_in_order(db):
        offering = ClassOfferingFactory(image=_image_file("hero.png"))
        ClassImageFactory(class_offering=offering, image=_image_file("g1.png"), sort_order=1, alt_text="g1")
        ClassImageFactory(class_offering=offering, image=_image_file("g2.png"), sort_order=2, alt_text="g2")
        items = offering.display_images
        assert len(items) == 3
        assert items[0]["alt"] == offering.title  # hero uses title
        assert items[1]["alt"] == "g1"
        assert items[2]["alt"] == "g2"

    def it_falls_back_to_category_hero_when_no_images(db):
        category = CategoryFactory(hero_image=_image_file("cat.png"))
        offering = ClassOfferingFactory(category=category, image="")
        items = offering.display_images
        assert len(items) == 1
        assert items[0]["alt"] == category.name

    def it_returns_empty_when_no_images_anywhere(db):
        offering = ClassOfferingFactory(image="")
        # CategoryFactory by default has no hero_image, so this should be empty.
        assert offering.display_images == []
