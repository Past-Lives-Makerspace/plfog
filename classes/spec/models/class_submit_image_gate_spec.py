"""BDD specs for the mandatory-photo gate on ClassOffering.submit_for_review.

Submitting a class for review requires the class's OWN hero (``image``) or at
least one gallery photo. The Category/Guild-Type hero fallback used by
``display_images`` does NOT satisfy the gate. "Three or more photos" is only a
soft nudge and never blocks submission.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from classes.factories import CategoryFactory, ClassImageFactory, ClassOfferingFactory
from classes.models import ClassOffering, CmsActivity


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    # Minimal PNG signature — enough for ImageField without a validating PIL pass.
    buf = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _add_gallery(offering: ClassOffering, count: int) -> None:
    for i in range(count):
        ClassImageFactory(class_offering=offering, image=_image_file(f"g{i}.png"), sort_order=i)


def describe_ClassOffering_photo_gate():
    def describe_has_submittable_image():
        def it_is_true_with_own_hero_and_no_gallery(db):
            offering = ClassOfferingFactory(image=_image_file("hero.png"))
            assert offering.has_submittable_image is True

        def it_is_true_with_own_hero_and_three_gallery(db):
            offering = ClassOfferingFactory(image=_image_file("hero.png"))
            _add_gallery(offering, 3)
            assert offering.has_submittable_image is True

        def it_is_false_with_no_hero_and_no_gallery(db):
            offering = ClassOfferingFactory(image="")
            assert offering.has_submittable_image is False

        def it_is_true_with_no_hero_and_one_gallery(db):
            offering = ClassOfferingFactory(image="")
            _add_gallery(offering, 1)
            assert offering.has_submittable_image is True

        def it_is_true_with_no_hero_and_two_gallery(db):
            offering = ClassOfferingFactory(image="")
            _add_gallery(offering, 2)
            assert offering.has_submittable_image is True

        def it_ignores_the_category_hero_fallback(db):
            category = CategoryFactory(hero_image=_image_file("cat.png"))
            offering = ClassOfferingFactory(image="", category=category)
            assert offering.has_submittable_image is False

    def describe_needs_photo_nudge():
        def it_is_true_with_zero_gallery(db):
            offering = ClassOfferingFactory(image=_image_file("hero.png"))
            assert offering.needs_photo_nudge is True

        def it_is_true_with_one_gallery(db):
            offering = ClassOfferingFactory(image="")
            _add_gallery(offering, 1)
            assert offering.needs_photo_nudge is True

        def it_is_true_with_two_gallery(db):
            offering = ClassOfferingFactory(image="")
            _add_gallery(offering, 2)
            assert offering.needs_photo_nudge is True

        def it_is_false_with_three_gallery(db):
            offering = ClassOfferingFactory(image="")
            _add_gallery(offering, 3)
            assert offering.needs_photo_nudge is False

        def it_is_false_with_four_gallery(db):
            offering = ClassOfferingFactory(image="")
            _add_gallery(offering, 4)
            assert offering.needs_photo_nudge is False

    def describe_submit_for_review():
        def it_raises_and_stays_draft_when_no_hero_and_no_gallery(db):
            offering = ClassOfferingFactory(image="", status=ClassOffering.Status.DRAFT)
            with pytest.raises(ValidationError):
                offering.submit_for_review()
            offering.refresh_from_db()
            # Nothing persisted or notified: status untouched, no gate opened, no
            # submission activity, no reviewer email.
            assert offering.status == ClassOffering.Status.DRAFT
            assert offering.approvals.count() == 0
            assert not CmsActivity.objects.filter(
                kind=CmsActivity.Kind.CLASS_SUBMITTED, class_offering=offering
            ).exists()
            assert mail.outbox == []

        def it_still_raises_when_only_the_category_has_a_hero(db):
            category = CategoryFactory(hero_image=_image_file("cat.png"))
            offering = ClassOfferingFactory(image="", category=category, status=ClassOffering.Status.DRAFT)
            with pytest.raises(ValidationError):
                offering.submit_for_review()
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.DRAFT
            assert offering.approvals.count() == 0

        def it_succeeds_with_own_hero_and_no_gallery(db):
            offering = ClassOfferingFactory(image=_image_file("hero.png"), status=ClassOffering.Status.DRAFT)
            rows = offering.submit_for_review()
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PENDING
            assert len(rows) == 1

        def it_succeeds_with_gallery_and_no_own_hero(db):
            offering = ClassOfferingFactory(image="", status=ClassOffering.Status.DRAFT)
            _add_gallery(offering, 1)
            rows = offering.submit_for_review()
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PENDING
            assert len(rows) == 1
