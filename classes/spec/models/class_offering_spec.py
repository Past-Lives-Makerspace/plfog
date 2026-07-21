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

    def describe_sale_is_active():
        def it_is_true_when_enabled_paid_and_percent_set(db):
            c = ClassOfferingFactory(sale_enabled=True, sale_kind=ClassOffering.SaleKind.PERCENT, sale_percent=20)
            assert c.sale_is_active is True

        def it_is_true_when_enabled_paid_and_fixed_amount_set(db):
            c = ClassOfferingFactory(sale_enabled=True, sale_kind=ClassOffering.SaleKind.FIXED, sale_amount_cents=1500)
            assert c.sale_is_active is True

        def it_is_false_when_disabled(db):
            c = ClassOfferingFactory(sale_enabled=False, sale_percent=20)
            assert c.sale_is_active is False

        def it_is_false_for_a_free_class(db):
            c = ClassOfferingFactory(price_cents=0, sale_enabled=True, sale_percent=20)
            assert c.sale_is_active is False

        def it_is_false_when_the_matching_percent_is_missing(db):
            c = ClassOfferingFactory(sale_enabled=True, sale_kind=ClassOffering.SaleKind.PERCENT, sale_percent=None)
            assert c.sale_is_active is False

        def it_is_false_when_the_matching_fixed_amount_is_missing(db):
            c = ClassOfferingFactory(sale_enabled=True, sale_kind=ClassOffering.SaleKind.FIXED, sale_amount_cents=None)
            assert c.sale_is_active is False

    def describe_sale_price_cents():
        def it_applies_a_percent_sale(db):
            c = ClassOfferingFactory(price_cents=5000, sale_enabled=True, sale_percent=20)
            assert c.sale_price_cents == 4000

        def it_applies_a_fixed_sale(db):
            c = ClassOfferingFactory(
                price_cents=5000, sale_enabled=True, sale_kind=ClassOffering.SaleKind.FIXED, sale_amount_cents=1500
            )
            assert c.sale_price_cents == 3500

        def it_equals_price_cents_when_no_sale_is_active(db):
            c = ClassOfferingFactory(price_cents=5000, sale_enabled=False)
            assert c.sale_price_cents == 5000

    def describe_sale_savings_display():
        def it_formats_a_percent_sale(db):
            c = ClassOfferingFactory(sale_enabled=True, sale_percent=20)
            assert c.sale_savings_display == "20% off"

        def it_formats_a_whole_dollar_fixed_sale(db):
            c = ClassOfferingFactory(sale_enabled=True, sale_kind=ClassOffering.SaleKind.FIXED, sale_amount_cents=1500)
            assert c.sale_savings_display == "$15 off"

        def it_keeps_cents_on_a_non_whole_fixed_sale(db):
            c = ClassOfferingFactory(sale_enabled=True, sale_kind=ClassOffering.SaleKind.FIXED, sale_amount_cents=1550)
            assert c.sale_savings_display == "$15.50 off"

        def it_is_empty_off_sale(db):
            c = ClassOfferingFactory(sale_enabled=False)
            assert c.sale_savings_display == ""

    def describe_sale_banner_display():
        def it_returns_the_custom_text(db):
            c = ClassOfferingFactory(sale_banner_text="Summer blowout!")
            assert c.sale_banner_display == "Summer blowout!"

        def it_falls_back_to_the_default_when_blank(db):
            from classes.models import DEFAULT_SALE_BANNER_TEXT

            c = ClassOfferingFactory(sale_banner_text="   ")
            assert c.sale_banner_display == DEFAULT_SALE_BANNER_TEXT

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
