"""BDD specs for the CmsActivity feed and its write-through hooks."""

from __future__ import annotations

from classes.factories import (
    ClassOfferingFactory,
    DiscountCodeFactory,
    RegistrationFactory,
)
from classes.models import ClassOffering, CmsActivity, Registration


def describe_CmsActivity():
    def describe_classoffering_hooks():
        def it_logs_class_created_on_first_save(db):
            offering = ClassOfferingFactory()
            assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CREATED, class_offering=offering).exists()

        def it_logs_class_submitted_on_submit_for_review(db):
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            offering.submit_for_review()
            assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_SUBMITTED, class_offering=offering).exists()

        def it_logs_class_archived_on_archive(db):
            offering = ClassOfferingFactory()
            offering.archive()
            assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_ARCHIVED, class_offering=offering).exists()

    def describe_registration_hooks():
        def it_logs_registration_created_on_create(db):
            offering = ClassOfferingFactory()
            reg = RegistrationFactory(class_offering=offering, email="ab@example.com")
            assert CmsActivity.objects.filter(kind=CmsActivity.Kind.REGISTRATION_CREATED, registration=reg).exists()

        def it_logs_waitlist_joined_when_status_is_waitlisted(db):
            offering = ClassOfferingFactory()
            reg = RegistrationFactory(
                class_offering=offering, email="wl@example.com", status=Registration.Status.WAITLISTED
            )
            assert CmsActivity.objects.filter(kind=CmsActivity.Kind.WAITLIST_JOINED, registration=reg).exists()

        def it_logs_registration_cancelled_on_cancel(db):
            offering = ClassOfferingFactory()
            reg = RegistrationFactory(
                class_offering=offering, email="cx@example.com", status=Registration.Status.CONFIRMED
            )
            reg.cancel(reason="changed mind")
            assert CmsActivity.objects.filter(kind=CmsActivity.Kind.REGISTRATION_CANCELLED, registration=reg).exists()

    def describe_discount_code_hooks():
        def it_logs_discount_code_created(db):
            code = DiscountCodeFactory()
            assert CmsActivity.objects.filter(
                kind=CmsActivity.Kind.DISCOUNT_CODE_CREATED,
                payload__code=code.code,
            ).exists()
