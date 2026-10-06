"""BDD specs for ClassImage (its focus included) and ClassOffering.display_images."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from classes.factories import CategoryFactory, ClassImageFactory, ClassOfferingFactory
from classes.models import MAX_GALLERY_IMAGES, ClassImage


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    # A real PNG: the gallery refuses bytes Pillow cannot open (#498).
    buf = BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def describe_ClassImage():
    def it_orders_by_sort_then_created(db):
        offering = ClassOfferingFactory(gallery=0)
        a = ClassImageFactory(class_offering=offering, image=_image_file("a.png"), sort_order=2)
        b = ClassImageFactory(class_offering=offering, image=_image_file("b.png"), sort_order=1)
        ordered = list(offering.gallery_images.all())
        assert ordered == [b, a]

    def it_cascades_when_offering_is_deleted(db):
        offering = ClassOfferingFactory(gallery=0)
        ClassImageFactory(class_offering=offering, image=_image_file())
        ClassImageFactory(class_offering=offering, image=_image_file("b.png"))
        offering_pk = offering.pk
        offering.delete()

        assert ClassImage.objects.filter(class_offering_id=offering_pk).count() == 0

    def describe_clean():
        def it_rejects_the_11th_image(db):
            offering = ClassOfferingFactory(gallery=0)
            for i in range(MAX_GALLERY_IMAGES):
                ClassImageFactory(class_offering=offering, image=_image_file(f"{i}.png"), sort_order=i)
            eleventh = ClassImage(class_offering=offering, image=_image_file("x.png"))
            with pytest.raises(ValidationError):
                eleventh.full_clean()

        def it_allows_the_10th_image(db):
            offering = ClassOfferingFactory(gallery=0)
            for i in range(MAX_GALLERY_IMAGES - 1):
                ClassImageFactory(class_offering=offering, image=_image_file(f"{i}.png"), sort_order=i)
            tenth = ClassImage(class_offering=offering, image=_image_file("ten.png"))
            tenth.full_clean()  # must not raise

        def it_allows_resaving_an_existing_image_at_the_cap(db):
            offering = ClassOfferingFactory(gallery=0)
            images = [
                ClassImageFactory(class_offering=offering, image=_image_file(f"{i}.png"), sort_order=i)
                for i in range(MAX_GALLERY_IMAGES)
            ]
            images[0].alt_text = "updated"
            images[0].full_clean()  # excludes self.pk — must not raise


def describe_add_gallery_images():
    def it_refuses_the_whole_batch_when_one_file_is_not_an_image(db):
        offering = ClassOfferingFactory()
        before = ClassImage.objects.filter(class_offering=offering).count()
        text = SimpleUploadedFile("notes.png", b"just some notes", content_type="image/png")

        with pytest.raises(ValidationError, match="not a photo we can open"):
            offering.add_gallery_images([_image_file("a.png"), text])
        assert ClassImage.objects.filter(class_offering=offering).count() == before

    def it_creates_rows_for_each_file(db):
        offering = ClassOfferingFactory(gallery=0)
        offering.add_gallery_images([_image_file("a.png"), _image_file("b.png")])
        assert offering.gallery_images.count() == 2

    def it_rejects_a_batch_that_exceeds_the_cap(db):
        offering = ClassOfferingFactory(gallery=0)
        files = [_image_file(f"{i}.png") for i in range(MAX_GALLERY_IMAGES + 1)]
        with pytest.raises(ValidationError):
            offering.add_gallery_images(files)
        assert offering.gallery_images.count() == 0  # atomic: nothing created

    def it_rejects_when_existing_plus_batch_exceeds_cap(db):
        offering = ClassOfferingFactory(gallery=0)
        for i in range(MAX_GALLERY_IMAGES - 1):
            ClassImageFactory(class_offering=offering, image=_image_file(f"e{i}.png"), sort_order=i)
        with pytest.raises(ValidationError):
            offering.add_gallery_images([_image_file("x.png"), _image_file("y.png")])
        assert offering.gallery_images.count() == MAX_GALLERY_IMAGES - 1  # batch rejected whole

    def it_appends_after_existing_images(db):
        offering = ClassOfferingFactory(gallery=0)
        ClassImageFactory(class_offering=offering, image=_image_file("first.png"), sort_order=0)
        offering.add_gallery_images([_image_file("second.png")])
        orders = list(offering.gallery_images.order_by("sort_order").values_list("sort_order", flat=True))
        assert orders == [0, 1]  # appended, not colliding at 0


def describe_object_position():
    def it_is_the_centre_with_no_focus(db):
        assert ClassImageFactory().object_position == "50% 50%"

    @pytest.mark.parametrize(("x", "y"), [(30, None), (None, 70)])
    def it_is_the_centre_when_only_one_axis_is_set(db, x, y):
        assert ClassImageFactory(focus_x=x, focus_y=y).object_position == "50% 50%"

    def it_is_the_focal_point_as_percentages(db):
        assert ClassImageFactory(focus_x=0, focus_y=100).object_position == "0% 100%"


def describe_set_focus():
    def it_stores_the_point(db):
        image = ClassImageFactory()
        image.set_focus((15, 85))
        image.refresh_from_db()
        assert (image.focus_x, image.focus_y) == (15, 85)

    def it_clears_both_columns_on_none(db):
        image = ClassImageFactory(focus_x=15, focus_y=85)
        image.set_focus(None)
        image.refresh_from_db()
        assert (image.focus_x, image.focus_y) == (None, None)

    def it_writes_only_the_focus_columns(db):
        image = ClassImageFactory(alt_text="kept")
        ClassImage.objects.filter(pk=image.pk).update(alt_text="changed elsewhere")
        image.set_focus((1, 2))
        image.refresh_from_db()
        assert image.alt_text == "changed elsewhere"

    def it_refuses_a_value_over_100_in_full_clean(db):
        image = ClassImageFactory(focus_x=101, focus_y=50)
        with pytest.raises(ValidationError) as exc:
            image.full_clean()
        assert "focus_x" in exc.value.message_dict


def describe_display_positions():
    def it_gives_each_gallery_row_its_own_position(db):
        offering = ClassOfferingFactory(gallery=0)
        ClassImageFactory(class_offering=offering, image=_image_file("a.png"), sort_order=0, focus_x=10, focus_y=20)
        ClassImageFactory(class_offering=offering, image=_image_file("b.png"), sort_order=1)
        assert [item["position"] for item in offering.gallery_display_images] == ["10% 20%", "50% 50%"]

    def it_gives_the_hero_the_banner_position_in_display_images(db):
        offering = ClassOfferingFactory(image=_image_file("hero.png"), gallery=0, hero_crop_x=25, hero_crop_y=75)
        ClassImageFactory(class_offering=offering, image=_image_file("g.png"), focus_x=90, focus_y=5)
        items = offering.display_images
        # Focal point mode (no box width): the banner's own position, not the centre.
        assert items[0]["position"] == "25% 75%"
        assert items[1]["position"] == "90% 5%"

    def it_pairs_the_hero_url_with_its_position_when_the_hero_is_cropped(db):
        offering = ClassOfferingFactory(
            image__width=1000,
            image__height=600,
            gallery=0,
            hero_crop_x=0,
            hero_crop_y=0,
            hero_crop_w=400,
            hero_crop_h=225,
        )
        assert offering.hero_cropped
        hero = offering.display_images[0]
        # The copy cut to the box is shown, so it sits at its centre, not at the box centre on the original.
        assert hero["url"] == offering.hero_cropped.url
        assert hero["url"] != offering.image.url
        assert hero["position"] == "50% 50%"

    def it_centres_the_category_fallback(db):
        category = CategoryFactory(hero_image=_image_file("cat.png"))
        offering = ClassOfferingFactory(category=category, image="", gallery=0)
        assert offering.display_images[0]["position"] == "50% 50%"


def describe_display_images():
    def it_returns_hero_then_gallery_in_order(db):
        offering = ClassOfferingFactory(image=_image_file("hero.png"), gallery=0)
        ClassImageFactory(class_offering=offering, image=_image_file("g1.png"), sort_order=1, alt_text="g1")
        ClassImageFactory(class_offering=offering, image=_image_file("g2.png"), sort_order=2, alt_text="g2")
        items = offering.display_images
        assert len(items) == 3
        assert items[0]["alt"] == offering.title  # hero uses title
        assert items[1]["alt"] == "g1"
        assert items[2]["alt"] == "g2"

    def it_falls_back_to_category_hero_when_no_images(db):
        category = CategoryFactory(hero_image=_image_file("cat.png"))
        offering = ClassOfferingFactory(category=category, image="", gallery=0)
        items = offering.display_images
        assert len(items) == 1
        assert items[0]["alt"] == category.name

    def it_returns_empty_when_no_images_anywhere(db):
        offering = ClassOfferingFactory(image="", gallery=0)
        # CategoryFactory by default has no hero_image, so this should be empty.
        assert offering.display_images == []
