"""BDD specs for ClassOffering."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from classes.factories import ClassOfferingFactory, InstructorFactory
from classes.models import ClassOffering


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def describe_ClassOffering():
    def it_stringifies_as_title(db):
        c = ClassOfferingFactory(title="Intro to Pottery")
        assert str(c) == "Intro to Pottery"

    def describe_state_transitions():
        def it_submits_draft_for_review(db):
            c = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            c.submit_for_review()
            c.refresh_from_db()
            assert c.status == ClassOffering.Status.PENDING

        def it_refuses_to_submit_non_draft(db):
            c = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            with pytest.raises(ValueError):
                c.submit_for_review()

        def it_approves_pending_and_sets_published_at(db, admin_user):
            c = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
            c.approve(admin_user)
            c.refresh_from_db()
            assert c.status == ClassOffering.Status.PUBLISHED
            assert c.published_at is not None
            assert c.approved_by_id == admin_user.pk

        def it_refuses_to_approve_non_pending(db, admin_user):
            c = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            with pytest.raises(ValueError):
                c.approve(admin_user)

        def it_archives_from_any_status(db):
            c = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            c.archive()
            c.refresh_from_db()
            assert c.status == ClassOffering.Status.ARCHIVED

    def describe_member_price_cents():
        def it_returns_discounted_cents_when_there_is_a_member_discount(db):
            c = ClassOfferingFactory(price_cents=10_000, member_discount_pct=10)
            assert c.member_price_cents == 9_000

        def it_returns_none_when_there_is_no_member_discount(db):
            c = ClassOfferingFactory(price_cents=10_000, member_discount_pct=0)
            assert c.member_price_cents is None

    def describe_manager():
        def it_public_filters_to_published(db):
            ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            assert ClassOffering.objects.public().count() == 1

        def it_pending_review_filters(db):
            ClassOfferingFactory(status=ClassOffering.Status.PENDING)
            ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            assert ClassOffering.objects.pending_review().count() == 1

        def it_for_instructor_filters(db):
            instructor = InstructorFactory()
            ClassOfferingFactory(instructor=instructor)
            ClassOfferingFactory()
            assert ClassOffering.objects.for_instructor(instructor).count() == 1

    def describe_add_gallery_images():
        def it_creates_class_images_with_sort_order(db):
            offering = ClassOfferingFactory()
            files = [_image_file("a.png"), _image_file("b.png"), _image_file("c.png")]
            offering.add_gallery_images(files)
            images = list(offering.gallery_images.all())
            assert len(images) == 3
            assert [img.sort_order for img in images] == [0, 1, 2]

        def it_handles_empty_list(db):
            offering = ClassOfferingFactory()
            offering.add_gallery_images([])
            assert offering.gallery_images.count() == 0
