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
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
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

    def describe_registration_actor():
        def it_records_the_acting_user_on_cancel(db):
            from django.contrib.auth import get_user_model

            actor = get_user_model().objects.create_user(username="canceller@example.com")
            reg = RegistrationFactory(status=Registration.Status.CONFIRMED, email="ac@example.com")
            reg.cancel(reason="admin pull", actor=actor)
            row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_CANCELLED, registration=reg)
            assert row.actor == actor

        def it_defaults_cancel_actor_to_none(db):
            reg = RegistrationFactory(status=Registration.Status.CONFIRMED, email="an@example.com")
            reg.cancel(reason="no actor")
            row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_CANCELLED, registration=reg)
            assert row.actor is None

        def it_records_the_acting_user_on_confirm(db):
            from django.contrib.auth import get_user_model

            actor = get_user_model().objects.create_user(username="confirmer@example.com")
            reg = RegistrationFactory(status=Registration.Status.PENDING, email="cf@example.com")
            reg._acting_user = actor
            reg.status = Registration.Status.CONFIRMED
            reg.save(update_fields=["status"])
            row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_CONFIRMED, registration=reg)
            assert row.actor == actor

        def it_leaves_confirm_actor_none_when_unset(db):
            reg = RegistrationFactory(status=Registration.Status.PENDING, email="cu@example.com")
            reg.status = Registration.Status.CONFIRMED
            reg.save(update_fields=["status"])
            row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_CONFIRMED, registration=reg)
            assert row.actor is None

        def it_records_the_acting_user_on_refund(db):
            from django.contrib.auth import get_user_model

            actor = get_user_model().objects.create_user(username="refunder@example.com")
            reg = RegistrationFactory(status=Registration.Status.CONFIRMED, email="rf@example.com")
            reg._acting_user = actor
            reg.status = Registration.Status.REFUNDED
            reg.save(update_fields=["status"])
            row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_REFUNDED, registration=reg)
            assert row.actor == actor

    def describe_discount_code_hooks():
        def it_logs_discount_code_created(db):
            code = DiscountCodeFactory()
            assert CmsActivity.objects.filter(
                kind=CmsActivity.Kind.DISCOUNT_CODE_CREATED,
                payload__code=code.code,
            ).exists()
