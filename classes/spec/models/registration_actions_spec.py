"""BDD specs for the admin registration actions: Registration.mark_refunded / move_to."""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory, RegistrationFactory, UserFactory
from classes.models import CmsActivity, Registration

pytestmark = pytest.mark.django_db


def describe_mark_refunded():
    def it_sets_status_to_refunded():
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        reg.mark_refunded()
        reg.refresh_from_db()
        assert reg.status == Registration.Status.REFUNDED

    def it_records_the_reason():
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        reg.mark_refunded(reason="duplicate charge")
        reg.refresh_from_db()
        assert reg.cancellation_reason == "duplicate charge"

    def it_logs_a_refund_activity_attributed_to_the_actor():
        actor = UserFactory(username="refunder@example.com")
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        reg.mark_refunded(actor=actor)
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_REFUNDED, registration=reg)
        assert row.actor == actor

    def it_frees_the_spot_and_promotes_the_waitlist():
        offering = ClassOfferingFactory(capacity=1)
        holder = RegistrationFactory(
            class_offering=offering, status=Registration.Status.CONFIRMED, email="h@example.com"
        )
        waiting = RegistrationFactory(
            class_offering=offering, status=Registration.Status.WAITLISTED, email="w@example.com"
        )
        holder.mark_refunded()
        waiting.refresh_from_db()
        assert waiting.waitlist_notified_at is not None

    def it_does_not_promote_when_refunding_a_waitlisted_row():
        offering = ClassOfferingFactory(capacity=2)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, email="h@example.com")
        waiting = RegistrationFactory(
            class_offering=offering, status=Registration.Status.WAITLISTED, email="w@example.com"
        )
        other = RegistrationFactory(
            class_offering=offering, status=Registration.Status.WAITLISTED, email="o@example.com"
        )
        waiting.mark_refunded()  # a waitlisted row holds no real spot → nothing to promote
        other.refresh_from_db()
        assert other.waitlist_notified_at is None


def describe_move_to():
    def it_reassigns_the_class():
        src = ClassOfferingFactory(slug="mv-src")
        dst = ClassOfferingFactory(slug="mv-dst")
        reg = RegistrationFactory(class_offering=src, status=Registration.Status.CONFIRMED)
        reg.move_to(dst)
        reg.refresh_from_db()
        assert reg.class_offering_id == dst.pk

    def it_keeps_the_amount_paid():
        src = ClassOfferingFactory(slug="mv-keep-src")
        dst = ClassOfferingFactory(slug="mv-keep-dst")
        reg = RegistrationFactory(class_offering=src, amount_paid_cents=4200, status=Registration.Status.CONFIRMED)
        reg.move_to(dst)
        reg.refresh_from_db()
        assert reg.amount_paid_cents == 4200

    def it_logs_a_move_activity_with_from_and_to():
        src = ClassOfferingFactory(slug="mv-log-src", title="Old Class")
        dst = ClassOfferingFactory(slug="mv-log-dst", title="New Class")
        actor = UserFactory(username="mover@example.com")
        reg = RegistrationFactory(class_offering=src, status=Registration.Status.CONFIRMED)
        reg.move_to(dst, actor=actor)
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_MOVED, registration=reg)
        assert row.actor == actor
        assert row.payload == {"from": "Old Class", "to": "New Class"}

    def it_promotes_the_source_waitlist_when_a_spot_frees():
        src = ClassOfferingFactory(slug="mv-wl-src", capacity=1)
        dst = ClassOfferingFactory(slug="mv-wl-dst")
        holder = RegistrationFactory(class_offering=src, status=Registration.Status.CONFIRMED, email="h@example.com")
        waiting = RegistrationFactory(class_offering=src, status=Registration.Status.WAITLISTED, email="w@example.com")
        holder.move_to(dst)
        waiting.refresh_from_db()
        assert waiting.waitlist_notified_at is not None

    def it_does_not_promote_when_moving_a_waitlisted_row():
        src = ClassOfferingFactory(slug="mv-wl2-src", capacity=2)
        dst = ClassOfferingFactory(slug="mv-wl2-dst")
        RegistrationFactory(class_offering=src, status=Registration.Status.CONFIRMED, email="h@example.com")
        waiting = RegistrationFactory(class_offering=src, status=Registration.Status.WAITLISTED, email="w@example.com")
        other = RegistrationFactory(class_offering=src, status=Registration.Status.WAITLISTED, email="o@example.com")
        waiting.move_to(dst)  # a waitlisted row holds no spot in src → nothing to promote
        other.refresh_from_db()
        assert other.waitlist_notified_at is None

    def it_raises_on_a_same_class_move():
        offering = ClassOfferingFactory(slug="mv-same")
        reg = RegistrationFactory(class_offering=offering)
        with pytest.raises(ValueError):
            reg.move_to(offering)
