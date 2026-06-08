"""BDD specs for the waitlist workflow."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import Registration


def _make_reg(offering, status=Registration.Status.CONFIRMED, email_n=0, when=None):
    reg = RegistrationFactory(
        class_offering=offering,
        last_name=f"Last{email_n}",
        email=f"r{email_n}@example.com",
        status=status,
    )
    if when:
        Registration.objects.filter(pk=reg.pk).update(registered_at=when)
        reg.refresh_from_db()
    return reg


def describe_waitlist():
    def describe_waitlist_position():
        def it_orders_by_registered_at(db):
            offering = ClassOfferingFactory(capacity=2)
            now = timezone.now()
            first = _make_reg(offering, Registration.Status.WAITLISTED, 1, now)
            second = _make_reg(offering, Registration.Status.WAITLISTED, 2, now + timedelta(minutes=5))
            assert first.waitlist_position == 1
            assert second.waitlist_position == 2

        def it_returns_none_for_non_waitlisted(db):
            offering = ClassOfferingFactory()
            reg = _make_reg(offering, Registration.Status.CONFIRMED, 1)
            assert reg.waitlist_position is None

    def describe_promote_next_from_waitlist():
        def it_promotes_oldest_unnotified_waitlist_row(db, mailoutbox):
            offering = ClassOfferingFactory(capacity=1)
            # One spot filled, one person waiting
            holder = _make_reg(offering, Registration.Status.CONFIRMED, 1)
            waiting = _make_reg(offering, Registration.Status.WAITLISTED, 2)
            # Cancel the held spot — should promote the waiter
            holder.cancel(reason="changed plans")
            waiting.refresh_from_db()
            assert waiting.waitlist_notified_at is not None

        def it_does_nothing_when_no_spot_actually_opens(db):
            offering = ClassOfferingFactory(capacity=1)
            _make_reg(offering, Registration.Status.CONFIRMED, 1)  # the only spot is taken
            waiting = _make_reg(offering, Registration.Status.WAITLISTED, 2)
            # spots_remaining == 0 → promote must noop and notify no one.
            result = offering.promote_next_from_waitlist()
            waiting.refresh_from_db()
            assert result is None
            assert waiting.waitlist_notified_at is None

        def it_skips_already_notified_waitlist_rows(db):
            offering = ClassOfferingFactory(capacity=1)
            _make_reg(offering, Registration.Status.CONFIRMED, 1)
            first = _make_reg(offering, Registration.Status.WAITLISTED, 2)
            first.waitlist_notified_at = timezone.now()
            first.save(update_fields=["waitlist_notified_at"])
            _make_reg(offering, Registration.Status.WAITLISTED, 3)
            # Cancel the confirmed reg — first is already notified, so promote
            # should look past first and notify the third registrant.
            Registration.objects.get(email="r1@example.com").cancel()
            third = Registration.objects.get(email="r3@example.com")
            assert third.waitlist_notified_at is not None
