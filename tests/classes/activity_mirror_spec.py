"""BDD-style tests asserting that classes.activity.log() mirrors into SiteActivity."""

from __future__ import annotations

import pytest

from classes import activity as cms_activity
from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import CmsActivity, Registration
from core.models import SiteActivity

pytestmark = pytest.mark.django_db


def describe_activity_mirror_to_site_activity():
    def it_mirrors_class_published_to_site_activity():
        offering = ClassOfferingFactory()
        cms_activity.log(CmsActivity.Kind.CLASS_PUBLISHED, class_offering=offering)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_PUBLISHED).exists()

    def it_mirrors_class_submitted_to_site_activity():
        offering = ClassOfferingFactory()
        cms_activity.log(CmsActivity.Kind.CLASS_SUBMITTED, class_offering=offering)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_SUBMITTED).exists()

    def it_mirrors_class_approved_to_site_activity():
        offering = ClassOfferingFactory()
        cms_activity.log(CmsActivity.Kind.CLASS_APPROVED, class_offering=offering)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_APPROVED).exists()

    def it_mirrors_class_cancelled_to_site_activity():
        offering = ClassOfferingFactory()
        cms_activity.log(CmsActivity.Kind.CLASS_CANCELLED, class_offering=offering)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_CANCELLED).exists()

    def it_does_not_mirror_a_quiet_archive_as_a_cancel():
        offering = ClassOfferingFactory()
        SiteActivity.objects.all().delete()
        cms_activity.log(CmsActivity.Kind.CLASS_ARCHIVED, class_offering=offering)
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_CANCELLED).exists()

    def it_mirrors_registration_created_with_registration_as_target():
        offering = ClassOfferingFactory()
        reg = RegistrationFactory(class_offering=offering, email="reg@example.com")
        # registration_created is logged automatically on RegistrationFactory save;
        # grab the SiteActivity row created from that event
        assert SiteActivity.objects.filter(
            kind=SiteActivity.Kind.CLASS_REGISTERED,
        ).exists()
        # Verify the target is the registration object
        site_row = SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_REGISTERED).first()
        assert site_row is not None
        assert site_row.target == reg

    def it_mirrors_registration_cancelled_to_site_activity():
        offering = ClassOfferingFactory()
        reg = RegistrationFactory(
            class_offering=offering,
            email="cx@example.com",
            status=Registration.Status.CONFIRMED,
        )
        # clear the auto-created SiteActivity rows from factory
        SiteActivity.objects.all().delete()
        reg.cancel(reason="changed mind")
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_REGISTRATION_CANCELLED).exists()

    def it_mirrors_registration_refunded_as_refund_issued():
        offering = ClassOfferingFactory()
        reg = RegistrationFactory(
            class_offering=offering,
            email="rf@example.com",
            status=Registration.Status.CONFIRMED,
        )
        SiteActivity.objects.all().delete()
        cms_activity.log(CmsActivity.Kind.REGISTRATION_REFUNDED, registration=reg)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.REFUND_ISSUED).exists()

    def it_mirrors_waitlist_joined_to_site_activity():
        offering = ClassOfferingFactory()
        reg = RegistrationFactory(
            class_offering=offering,
            email="wl@example.com",
            status=Registration.Status.WAITLISTED,
        )
        # waitlist_joined is logged automatically; verify it exists
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_WAITLIST_JOINED).exists()
        site_row = SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_WAITLIST_JOINED).first()
        assert site_row is not None
        assert site_row.target == reg

    def it_does_not_mirror_unmapped_kinds():
        offering = ClassOfferingFactory()
        before_count = SiteActivity.objects.count()
        cms_activity.log(CmsActivity.Kind.DISCOUNT_CODE_CREATED, class_offering=offering)
        # Only the CmsActivity row is created; SiteActivity count unchanged
        assert SiteActivity.objects.count() == before_count
