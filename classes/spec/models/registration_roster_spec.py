"""BDD specs for the roster-management model layer: promote / mark-paid / remove / price mirror."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from classes.exceptions import RegistrationStateError
from classes.factories import ClassOfferingFactory, DiscountCodeFactory, InstructorFactory, RegistrationFactory
from classes.models import ClassOffering, CmsActivity, Registration

pytestmark = pytest.mark.django_db


def _waitlisted(**kwargs) -> Registration:
    kwargs.setdefault("status", Registration.Status.WAITLISTED)
    return RegistrationFactory(**kwargs)


def _member():

    return InstructorFactory(instructor_slug="")  # a plain active member row


def describe_promote_from_waitlist():
    def it_confirms_immediately_with_confirmed_at(db):
        reg = _waitlisted()
        reg.promote_from_waitlist(actor=None)
        reg.refresh_from_db()
        assert reg.status == Registration.Status.CONFIRMED
        assert reg.confirmed_at is not None

    def it_stamps_the_base_price(db):
        reg = _waitlisted(class_offering=ClassOfferingFactory(price_cents=5000, member_discount_pct=0))
        reg.promote_from_waitlist(actor=None)
        reg.refresh_from_db()
        assert reg.payment_due_cents == 5000

    def it_stamps_the_sale_price(db):
        offering = ClassOfferingFactory(
            price_cents=5000,
            member_discount_pct=0,
            sale_enabled=True,
            sale_kind=ClassOffering.SaleKind.PERCENT,
            sale_percent=20,
        )
        reg = _waitlisted(class_offering=offering)
        reg.promote_from_waitlist(actor=None)
        reg.refresh_from_db()
        assert reg.payment_due_cents == 4000

    def it_applies_the_member_discount_for_a_linked_member(db):
        offering = ClassOfferingFactory(price_cents=5000, member_discount_pct=10)
        reg = _waitlisted(class_offering=offering, member=_member())
        reg.promote_from_waitlist(actor=None)
        reg.refresh_from_db()
        assert reg.payment_due_cents == 4500

    def it_applies_a_stored_discount_code(db):
        offering = ClassOfferingFactory(price_cents=5000, member_discount_pct=0)
        code = DiscountCodeFactory(discount_pct=50)
        reg = _waitlisted(class_offering=offering, discount_code=code)
        reg.promote_from_waitlist(actor=None)
        reg.refresh_from_db()
        assert reg.payment_due_cents == 2500

    def it_stamps_zero_for_a_free_class(db):
        reg = _waitlisted(class_offering=ClassOfferingFactory(price_cents=0, member_discount_pct=0))
        reg.promote_from_waitlist(actor=None)
        reg.refresh_from_db()
        assert reg.payment_due_cents == 0

    def it_logs_waitlist_promoted_not_registration_confirmed(db, admin_user):
        reg = _waitlisted(class_offering=ClassOfferingFactory(price_cents=5000, member_discount_pct=0))
        reg.promote_from_waitlist(actor=admin_user)
        promoted = CmsActivity.objects.filter(kind=CmsActivity.Kind.WAITLIST_PROMOTED, registration=reg)
        assert promoted.count() == 1
        row = promoted.get()
        assert row.actor == admin_user
        assert row.payload == {"due_cents": 5000}
        assert not CmsActivity.objects.filter(kind=CmsActivity.Kind.REGISTRATION_CONFIRMED, registration=reg).exists()

    def it_raises_for_every_non_waitlisted_status(db):
        for status in (
            Registration.Status.PENDING,
            Registration.Status.CONFIRMED,
            Registration.Status.CANCELLED,
            Registration.Status.REFUNDED,
        ):
            reg = RegistrationFactory(status=status)
            with pytest.raises(RegistrationStateError):
                reg.promote_from_waitlist(actor=None)

    def it_allows_promoting_over_capacity(db):
        offering = ClassOfferingFactory(capacity=1)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        reg = _waitlisted(class_offering=offering)
        reg.promote_from_waitlist(actor=None)
        reg.refresh_from_db()
        assert reg.status == Registration.Status.CONFIRMED
        assert offering.spots_remaining == 0

    def it_never_fires_a_claim_link(db):
        offering = ClassOfferingFactory(capacity=5)
        _waitlisted(class_offering=offering, email="other@example.com")
        reg = _waitlisted(class_offering=offering)
        with patch("classes.emails.send_waitlist_spot_opened") as spot_opened:
            reg.promote_from_waitlist(actor=None)
        spot_opened.assert_not_called()

    def it_sends_no_email_itself(db, mailoutbox):
        reg = _waitlisted()
        reg.promote_from_waitlist(actor=None)
        assert len(mailoutbox) == 0

    def it_guards_on_a_locked_refetch_so_concurrent_promotes_cannot_both_pass(db):
        # A second staff member's stale in-memory copy still reads WAITLISTED;
        # the select_for_update refetch inside promote sees the flipped row.
        reg = _waitlisted(class_offering=ClassOfferingFactory(price_cents=5000, member_discount_pct=0))
        stale = Registration.objects.get(pk=reg.pk)
        reg.promote_from_waitlist(actor=None)
        assert stale.status == Registration.Status.WAITLISTED  # the stale copy would pass an in-memory guard
        with pytest.raises(RegistrationStateError):
            stale.promote_from_waitlist(actor=None)
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.WAITLIST_PROMOTED, registration=reg).count() == 1


def describe_is_unpaid_and_balance():
    def it_derives_the_balance_from_due_minus_paid(db):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=5000, amount_paid_cents=0)
        assert reg.balance_due_cents == 5000
        assert reg.is_unpaid is True

    def it_is_settled_when_paid_covers_due(db):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=5000, amount_paid_cents=5000)
        assert reg.balance_due_cents == 0
        assert reg.is_unpaid is False

    def it_floors_the_balance_at_zero_when_overpaid(db):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=4000, amount_paid_cents=5000)
        assert reg.balance_due_cents == 0
        assert reg.is_unpaid is False

    def it_never_marks_legacy_rows_unpaid(db):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=0, amount_paid_cents=0)
        assert reg.is_unpaid is False

    def it_requires_confirmed_status(db):
        reg = RegistrationFactory(status=Registration.Status.PENDING, payment_due_cents=5000, amount_paid_cents=0)
        assert reg.balance_due_cents == 5000
        assert reg.is_unpaid is False


def describe_mark_paid():
    def it_settles_the_balance(db):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=0)
        reg.mark_paid(actor=None, note="cash")
        reg.refresh_from_db()
        assert reg.amount_paid_cents == 4500
        assert reg.is_unpaid is False

    def it_logs_the_actor_and_note(db, admin_user):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=0)
        reg.mark_paid(actor=admin_user, note="comped")
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_MARKED_PAID, registration=reg)
        assert row.actor == admin_user
        assert row.payload == {"note": "comped"}

    def it_bumps_the_stored_discount_code_once(db):
        code = DiscountCodeFactory(discount_pct=10, use_count=0)
        reg = RegistrationFactory(
            status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=0, discount_code=code
        )
        reg.mark_paid(actor=None)
        code.refresh_from_db()
        assert code.use_count == 1
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.DISCOUNT_CODE_REDEEMED, registration=reg).count() == 1

    def it_skips_the_code_bump_when_a_partial_amount_was_already_recorded(db):
        code = DiscountCodeFactory(discount_pct=10, use_count=0)
        reg = RegistrationFactory(
            status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=1000, discount_code=code
        )
        reg.mark_paid(actor=None)
        code.refresh_from_db()
        assert code.use_count == 0  # the earlier recorded payment already counted it

    def it_raises_when_nothing_is_owed(db):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=0, amount_paid_cents=0)
        with pytest.raises(RegistrationStateError):
            reg.mark_paid(actor=None)

    def it_raises_on_a_second_call(db):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=0)
        reg.mark_paid(actor=None)
        with pytest.raises(RegistrationStateError):
            reg.mark_paid(actor=None)

    def it_guards_on_a_locked_refetch_when_an_online_payment_settled_the_row_first(db):
        # The webhook committed between the view's fetch and this call: the stale
        # in-memory copy still reads unpaid, but the select_for_update refetch
        # sees the settled row — cash + online can never both record silently.
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=0)
        stale = Registration.objects.get(pk=reg.pk)
        Registration.objects.filter(pk=reg.pk).update(amount_paid_cents=4500)  # the webhook's settlement
        assert stale.is_unpaid  # the stale copy would pass an in-memory guard
        with pytest.raises(RegistrationStateError):
            stale.mark_paid(actor=None)
        reg.refresh_from_db()
        assert reg.amount_paid_cents == 4500  # the online settlement stands, recorded once
        assert not CmsActivity.objects.filter(kind=CmsActivity.Kind.REGISTRATION_MARKED_PAID, registration=reg).exists()

    def it_sends_no_email(db, mailoutbox):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=0)
        reg.mark_paid(actor=None)
        assert len(mailoutbox) == 0


def describe_remove_by_staff():
    def it_cancels_a_seat_holder_and_fires_the_claim_link(db, mailoutbox):
        offering = ClassOfferingFactory(capacity=1)
        holder = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        waiting = RegistrationFactory(
            class_offering=offering, status=Registration.Status.WAITLISTED, email="waiting@example.com"
        )
        holder.remove_by_staff(actor=None, reason="no-show")
        holder.refresh_from_db()
        waiting.refresh_from_db()
        assert holder.status == Registration.Status.CANCELLED
        assert holder.cancellation_reason == "no-show"
        assert waiting.waitlist_notified_at is not None

    def it_removes_a_waitlister_without_claim_links(db):
        offering = ClassOfferingFactory(capacity=1)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        waiting = RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        waiting.remove_by_staff(actor=None)
        waiting.refresh_from_db()
        assert waiting.status == Registration.Status.CANCELLED
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.WAITLIST_LEFT, registration=waiting).exists()
        assert not CmsActivity.objects.filter(kind=CmsActivity.Kind.WAITLIST_NOTIFIED).exists()

    def it_emails_the_seat_holder_with_the_refund_line_when_paid(db, mailoutbox):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, amount_paid_cents=4500)
        reg.remove_by_staff(actor=None)
        removal = [m for m in mailoutbox if "was cancelled" in m.subject]
        assert len(removal) == 1
        assert "refund is handled separately" in removal[0].body

    def it_omits_the_refund_line_when_nothing_was_paid(db, mailoutbox):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, amount_paid_cents=0)
        reg.remove_by_staff(actor=None)
        removal = [m for m in mailoutbox if "was cancelled" in m.subject]
        assert len(removal) == 1
        assert "refund" not in removal[0].body.lower()

    def it_uses_the_waitlist_email_fork_for_a_waitlister(db, mailoutbox):
        reg = RegistrationFactory(status=Registration.Status.WAITLISTED, amount_paid_cents=4500)
        reg.remove_by_staff(actor=None)
        removal = [m for m in mailoutbox if "removed from the waitlist" in m.subject]
        assert len(removal) == 1
        body = removal[0].body.lower()
        assert "seat" not in body
        assert "refund" not in body
        assert "cancelled" not in body

    def it_raises_when_already_cancelled(db):
        reg = RegistrationFactory(status=Registration.Status.CANCELLED)
        with pytest.raises(RegistrationStateError):
            reg.remove_by_staff(actor=None)

    def it_leaves_self_serve_cancel_email_free(db, mailoutbox):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        reg.cancel(reason="self-serve")
        assert all("cancelled" not in m.subject for m in mailoutbox)


def describe_compute_promote_price_cents():
    def it_orders_sale_then_member_then_code(db):
        offering = ClassOfferingFactory(
            price_cents=10000,
            member_discount_pct=10,
            sale_enabled=True,
            sale_kind=ClassOffering.SaleKind.PERCENT,
            sale_percent=20,
            sale_allow_discount_codes=True,
        )
        code = DiscountCodeFactory(discount_pct=50)
        reg = _waitlisted(class_offering=offering, member=_member(), discount_code=code)
        # 10000 → sale 20% → 8000 → member 10% → 7200 → code 50% → 3600
        assert reg.compute_promote_price_cents() == 3600

    def it_ignores_the_stored_code_when_the_sale_blocks_codes(db):
        offering = ClassOfferingFactory(
            price_cents=10000,
            member_discount_pct=0,
            sale_enabled=True,
            sale_kind=ClassOffering.SaleKind.PERCENT,
            sale_percent=20,
            sale_allow_discount_codes=False,
        )
        code = DiscountCodeFactory(discount_pct=50)
        reg = _waitlisted(class_offering=offering, discount_code=code)
        assert reg.compute_promote_price_cents() == 8000

    def it_returns_zero_for_a_hundred_percent_code_with_no_blocking_sale(db):
        offering = ClassOfferingFactory(price_cents=5000, member_discount_pct=0)
        code = DiscountCodeFactory(discount_pct=100)
        reg = _waitlisted(class_offering=offering, discount_code=code)
        assert reg.compute_promote_price_cents() == 0

    def it_floors_at_zero_for_a_fixed_code_bigger_than_the_price(db):
        offering = ClassOfferingFactory(price_cents=1000, member_discount_pct=0)
        code = DiscountCodeFactory(discount_pct=None, discount_fixed_cents=5000)
        reg = _waitlisted(class_offering=offering, discount_code=code)
        assert reg.compute_promote_price_cents() == 0
