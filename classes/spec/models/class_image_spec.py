"""BDD specs for ClassImage and ClassOffering.display_images."""

from __future__ import annotations

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile

from classes.factories import CategoryFactory, ClassImageFactory, ClassOfferingFactory


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
        from classes.models import ClassImage

        assert ClassImage.objects.filter(class_offering_id=offering_pk).count() == 0


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
        offering = ClassOfferingFactory(category=category)
        items = offering.display_images
        assert len(items) == 1
        assert items[0]["alt"] == category.name

    def it_returns_empty_when_no_images_anywhere(db):
        offering = ClassOfferingFactory()
        # CategoryFactory by default has no hero_image, so this should be empty.
        assert offering.display_images == []
