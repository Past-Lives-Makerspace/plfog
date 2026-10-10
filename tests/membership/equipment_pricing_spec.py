"""BDD specs for priced equipment reservations (#749, part 1): the price, the hold, paying and refunds.

The price math on the half hour grid and the donation rules; the hold made under the equipment
lock and its guards; the one finalize, exactly once from every road; the Stripe verified release,
Pay now and the sweep; the webhook handlers filtered on their kind; and the automatic full refund
on decline, manager cancel and member cancel, with the late fee kept separate. Stripe is mocked
the way the orientation checkout specs mock it. Names are factory strings no changelog could
contain (STANDARDS.md §8).
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
import stripe
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from billing.models import LateCancellationFee, PaymentRefund
from billing.refunds import source_field_name
from core.models import Notification, SiteConfiguration
from membership import equipment as equipment_service
from membership import webhook_handlers
from membership.models import (
    Equipment,
    EquipmentError,
    EquipmentReservation,
    Member,
    money_display,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

HOLD = EquipmentReservation.Status.PENDING_PAYMENT
_RESERVATIONS_WEBHOOK = "https://discord.com/api/webhooks/749/reservations"
_SESSION = {"id": "cs_res_1", "url": "https://checkout.stripe.example/cs_res_1"}


def _day(offset: int = 2):
    return timezone.localdate() + timedelta(days=offset)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _tool(**kwargs) -> Equipment:
    """A tool open 9 to 5 on the day two days out; pricing and approval from ``kwargs``."""
    equipment = EquipmentFactory(**kwargs)
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _hourly(rate_cents: int = 2500, **kwargs) -> Equipment:
    return _tool(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=rate_cents, **kwargs)


def _member(username: str = "brindle") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Quarrystone"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _retrieved(**overrides):
    session = {
        "id": "cs_res_1",
        "url": "https://checkout.stripe.example/cs_res_1",
        "status": "open",
        "payment_status": "unpaid",
        "payment_intent": "",
        "amount_total": None,
    }
    session.update(overrides)
    return session


def _paid_session(amount: int = 3750):
    return _retrieved(status="complete", payment_status="paid", payment_intent="pi_res_1", amount_total=amount)


def _hold(equipment: Equipment | None = None, *, member: Member | None = None, age_hours: float = 0, **kwargs):
    """An unpaid hold two days out, 10:00 to 11:30, made ``age_hours`` ago."""
    hold = EquipmentReservationFactory(
        equipment=equipment or _hourly(),
        member=member or _member("holdfast"),
        starts_at=_at(_day(), 10),
        ends_at=_at(_day(), 11, 30),
        status=HOLD,
        stripe_session_id=kwargs.pop("stripe_session_id", "cs_res_1"),
        **kwargs,
    )
    if age_hours:
        EquipmentReservation.objects.filter(pk=hold.pk).update(created_at=timezone.now() - timedelta(hours=age_hours))
        hold.refresh_from_db()
    return hold


def _paid_row(equipment: Equipment | None = None, *, status=EquipmentReservation.Status.CONFIRMED, **kwargs):
    return EquipmentReservationFactory(
        equipment=equipment or _hourly(),
        member=kwargs.pop("member", None) or _member("paidwell"),
        status=status,
        amount_paid_cents=kwargs.pop("amount_paid_cents", 5000),
        stripe_payment_id="pi_paid_1",
        stripe_session_id="cs_paid_1",
        **kwargs,
    )


def _refund_result(refund_id: str = "re_res_1"):
    return {"id": refund_id, "status": "succeeded", "amount": 5000}


def _event(**metadata):
    session = {
        "id": "cs_res_1",
        "payment_status": "paid",
        "payment_intent": "pi_res_1",
        "amount_total": 3750,
        "customer_email": "x@example.com",
        "metadata": {"kind": "equipment_reservation", **metadata},
    }
    return {"data": {"object": session}}


def describe_pricing():
    def it_is_free_by_default_and_charges_nothing():
        equipment = EquipmentFactory()
        assert equipment.pricing == Equipment.Pricing.FREE
        assert not equipment.is_priced
        assert equipment.price_chip == ""
        assert equipment.checkout_amount_cents(90, None) == 0
        assert equipment.charge_cents_for(90) == 0

    def it_is_free_for_a_row_inserted_without_the_new_columns():
        # The database default: the release still serving while this migrates inserts without pricing.
        equipment = EquipmentFactory()
        Equipment.objects.filter(pk=equipment.pk).update(name="Gribblewort Kiln")
        assert Equipment.objects.get(pk=equipment.pk).pricing == "free"

    def it_charges_rate_times_minutes_over_sixty_rounded_half_up_to_the_cent():
        equipment = EquipmentFactory(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500)
        assert equipment.charge_cents_for(90) == 3750
        assert equipment.charge_cents_for(30) == 1250
        odd = EquipmentFactory(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=101)
        assert odd.charge_cents_for(30) == 51  # 50.5 rounds up
        assert odd.charge_cents_for(90) == 152  # 151.5 rounds up
        assert EquipmentFactory(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=99).charge_cents_for(30) == 50

    def it_treats_an_hourly_item_with_no_rate_as_free():
        equipment = EquipmentFactory(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=0)
        assert not equipment.is_hourly
        assert equipment.charge_cents_for(60) == 0
        assert equipment.price_chip == ""

    def it_shows_the_hourly_chip_and_charges_the_rate_whatever_was_entered():
        equipment = EquipmentFactory(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500)
        assert equipment.price_chip == "$25 per hour"
        assert equipment.checkout_amount_cents(90, 1) == 3750

    def it_shows_the_donation_wording_on_the_chip():
        suggested = EquipmentFactory(pricing=Equipment.Pricing.DONATION, donation_suggested_cents=1000)
        assert suggested.price_chip == "Donation, $10 suggested"
        assert EquipmentFactory(pricing=Equipment.Pricing.DONATION).price_chip == "Pay what you can"

    def it_gives_the_suggestion_as_a_plain_number_for_the_amount_box():
        assert EquipmentFactory(donation_suggested_cents=1250).donation_suggested_dollars == "12.50"
        assert EquipmentFactory().donation_suggested_dollars == ""

    def it_hints_at_the_floor_or_the_minimum():
        assert EquipmentFactory().donation_amount_hint == (
            "$0 reserves for free. Any other amount is at least $1, paid by card when you reserve."
        )
        assert EquipmentFactory(donation_minimum_cents=500).donation_amount_hint == (
            "At least $5.00, paid by card when you reserve."
        )

    def describe_donation_amounts():
        def _donation(**kwargs) -> Equipment:
            return EquipmentFactory(pricing=Equipment.Pricing.DONATION, **kwargs)

        def it_reserves_free_at_zero_and_charges_what_was_entered():
            equipment = _donation()
            assert equipment.checkout_amount_cents(60, 0) == 0
            assert equipment.checkout_amount_cents(60, 1000) == 1000
            assert equipment.checkout_amount_cents(60, 100) == 100
            assert equipment.checkout_amount_cents(60, 50000) == 50000

        def it_refuses_fifty_cents_and_over_five_hundred_and_below_zero():
            equipment = _donation()
            for cents in (50, 99, 50001, -1):
                with pytest.raises(
                    EquipmentError, match=r"^Enter an amount from \$1 to \$500, or \$0 to reserve for free\.$"
                ):
                    equipment.checkout_amount_cents(60, cents)

        def it_asks_for_an_amount_when_none_was_sent():
            with pytest.raises(EquipmentError, match=r"^Enter what you'd like to pay\. \$0 is fine\.$"):
                _donation().checkout_amount_cents(60, None)
            with pytest.raises(EquipmentError, match=r"^Enter what you'd like to pay, \$5\.00 or more\.$"):
                _donation(donation_minimum_cents=500).checkout_amount_cents(60, None)

        def it_holds_the_member_to_the_minimum():
            equipment = _donation(donation_minimum_cents=500)
            with pytest.raises(EquipmentError, match=r"^The minimum here is \$5\.00\.$"):
                equipment.checkout_amount_cents(60, 0)
            assert equipment.checkout_amount_cents(60, 500) == 500

    def describe_booking_terms():
        def it_reads_one_line_for_each_pricing_and_approval_pair():
            hourly = EquipmentFactory(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500)
            donation = EquipmentFactory(pricing=Equipment.Pricing.DONATION)
            free = EquipmentFactory()
            instant = "You pay when you reserve. Cancel and you get an automatic full refund."
            approval = (
                "You pay when you reserve. A manager approves each reservation; if they decline, "
                "you get an automatic full refund."
            )
            assert hourly.booking_terms(needs_approval=False) == instant
            assert hourly.booking_terms(needs_approval=True) == approval
            assert donation.booking_terms(needs_approval=False) == instant
            assert donation.booking_terms(needs_approval=True) == approval
            assert free.booking_terms(needs_approval=True) == "A manager approves each reservation before it is booked."
            assert free.booking_terms(needs_approval=False) == ""

    def it_writes_money_with_cents_and_thousands():
        assert money_display(3750) == "$37.50"
        assert money_display(120000) == "$1,200.00"


def describe_reservation_money():
    def it_reads_what_was_paid_and_how_long_it_runs():
        row = _paid_row(amount_paid_cents=2500)
        assert row.paid_display == "$25.00"
        assert row.duration_minutes == 60
        assert EquipmentReservationFactory().paid_display == ""

    def it_releases_a_hold_two_hours_after_it_was_made():
        hold = _hold()
        assert hold.hold_released_by == hold.created_at + timedelta(hours=2)
        assert hold.is_awaiting_payment

    def describe_refund_protocol():
        def it_points_refunds_at_the_reservation_field():
            row = _paid_row()
            assert source_field_name(row) == "reservation"
            assert row.refund_payment_intent_id == "pi_paid_1"
            context = row.refund_receipt_context()
            assert context["item_title"] == f"{row.equipment.name} reservation"
            assert context["member"] == row.member
            assert context["in_app_url"] == f"/equipment/{row.equipment.slug}/"
            assert context["manage_url"].endswith(f"/equipment/{row.equipment.slug}/")
            row.on_fully_refunded("", None)  # nothing to do, and nothing raises
            assert row.status == EquipmentReservation.Status.CONFIRMED

        def it_tracks_the_refund_state_and_the_row_note():
            row = _paid_row()
            assert (row.refund_state, row.refund_note, row.refundable_cents) == ("none", "", 5000)
            PaymentRefund.objects.create(reservation=row, amount_cents=5000, status=PaymentRefund.Status.FAILED)
            row = EquipmentReservation.objects.get(pk=row.pk)
            assert row.refund_state == "failed"
            assert row.refund_note == "Your $50.00 refund is being processed."
            PaymentRefund.objects.create(reservation=row, amount_cents=2000, status=PaymentRefund.Status.SUCCEEDED)
            row = EquipmentReservation.objects.get(pk=row.pk)
            assert row.refund_state == "partial"
            assert row.refund_note == "Your $50.00 was refunded."
            PaymentRefund.objects.create(reservation=row, amount_cents=3000, status=PaymentRefund.Status.SUCCEEDED)
            row = EquipmentReservation.objects.get(pk=row.pk)
            assert (row.refund_state, row.refundable_cents) == ("full", 0)

        def it_has_no_refund_note_for_a_free_row():
            assert EquipmentReservationFactory().refund_note == ""

        def it_keeps_exactly_one_source_on_a_refund_row():
            row = _paid_row()
            fee = LateCancellationFee.objects.create(member=row.member, reservation=row, amount_cents=1000)
            with pytest.raises(IntegrityError), transaction.atomic():
                PaymentRefund.objects.create(reservation=row, late_fee=fee, amount_cents=100)
            refund = PaymentRefund.objects.create(reservation=row, amount_cents=100)
            assert (refund.source_object, refund.source_kind) == (row, "reservation")
            assert list(PaymentRefund.objects.for_source(row)) == [refund]

        def it_names_every_source_kind_and_sends_a_failed_reservation_refund_to_the_payments_panel():
            from billing import refunds

            assert PaymentRefund(registration_id=1).source_kind == "class"
            assert PaymentRefund(orientation_booking_id=1).source_kind == "orientation"
            assert PaymentRefund(reservation_id=1).source_kind == "reservation"
            assert PaymentRefund(late_fee_id=1).source_kind == "late_fee"
            refund = PaymentRefund.objects.create(reservation=_paid_row(), amount_cents=100)
            assert refunds._refund_admin_url(refund).endswith(
                "/billing/admin/dashboard/?tab=payments&source=reservation&status=failed"
            )

        @patch("billing.stripe_utils.create_refund", return_value=_refund_result())
        def it_issues_a_real_refund_through_the_shared_engine(mock_refund):
            row = _paid_row()
            refund = row.issue_refund(reason="sorry")
            assert refund.reservation == row
            assert refund.status == PaymentRefund.Status.SUCCEEDED
            assert mock_refund.call_args.kwargs["payment_intent_id"] == "pi_paid_1"


def describe_holding():
    def it_holds_its_time_like_a_confirmed_row():
        hold = _hold()
        assert EquipmentReservation.Status.PENDING_PAYMENT in EquipmentReservation.HOLDING_STATUSES
        assert list(EquipmentReservation.objects.overlapping(hold.equipment, _at(_day(), 9), _at(_day(), 12))) == [hold]
        with pytest.raises(EquipmentError, match="just taken"):
            hold.equipment.ensure_reservable(_member("latecomer"), _at(_day(), 11), 60)

    def it_counts_toward_the_member_cap():
        equipment = _hourly(max_active_reservations_per_member=1)
        member = _member("capped")
        _hold(equipment, member=member)
        with pytest.raises(EquipmentError, match="upcoming reservation"):
            equipment.ensure_reservable(member, _at(_day(), 14), 60)

    def it_is_never_in_use_now():
        hold = _hold()
        EquipmentReservation.objects.filter(pk=hold.pk).update(
            starts_at=timezone.now() - timedelta(minutes=5), ends_at=timezone.now() + timedelta(minutes=55)
        )
        assert not EquipmentReservation.objects.confirmed().filter(pk=hold.pk).exists()

    def it_keeps_every_holding_status_in_the_overlap_index():
        index = next(i for i in EquipmentReservation._meta.indexes if i.name == "idx_equipres_holding")
        assert index.fields == ["equipment", "starts_at"]
        assert dict(index.condition.children) == {"status__in": ["confirmed", "pending_approval", "pending_payment"]}
        assert set(dict(index.condition.children)["status__in"]) == set(EquipmentReservation.HOLDING_STATUSES)

    def it_refuses_a_plain_cancel_of_an_unpaid_hold():
        hold = _hold()
        with pytest.raises(EquipmentError, match="held while the member pays"):
            hold.cancel(hold.member)


def describe_reserve_on_priced_equipment():
    def it_never_books_an_hourly_item_for_free():
        equipment = _hourly()
        with pytest.raises(
            EquipmentError, match=r"^The price for this time just changed\. Please pick your time again\.$"
        ):
            equipment_service.reserve(equipment, _member(), _at(_day(), 14), 60)
        with pytest.raises(EquipmentError, match="price for this time just changed"):
            equipment_service.reserve(equipment, _member("zeroed"), _at(_day(), 14), 60, donation_cents=0)
        assert not EquipmentReservation.objects.exists()

    def it_books_a_donation_item_free_only_on_an_explicit_zero():
        equipment = _tool(pricing=Equipment.Pricing.DONATION)
        with pytest.raises(EquipmentError, match="price for this time just changed"):
            equipment_service.reserve(equipment, _member(), _at(_day(), 14), 60)
        with pytest.raises(EquipmentError, match="price for this time just changed"):
            equipment_service.reserve(equipment, _member("tenner"), _at(_day(), 15), 60, donation_cents=1000)
        booked = equipment_service.reserve(equipment, _member("zero"), _at(_day(), 16), 60, donation_cents=0)
        assert booked.status == EquipmentReservation.Status.CONFIRMED

    def it_still_books_a_free_item_with_no_amount():
        booked = equipment_service.reserve(_tool(), _member(), _at(_day(), 14), 60)
        assert booked.status == EquipmentReservation.Status.CONFIRMED

    def it_re_runs_the_price_under_the_lock_so_a_raised_minimum_refuses_a_zero():
        # The page read a $0 minimum; a manager raised it to $5 before the lock: the stale row would book free.
        stale = _tool(pricing=Equipment.Pricing.DONATION)
        Equipment.objects.filter(pk=stale.pk).update(donation_minimum_cents=500)
        with pytest.raises(EquipmentError, match=r"^The minimum here is \$5\.00\.$"):
            equipment_service.reserve(stale, _member(), _at(_day(), 14), 60, donation_cents=0)
        assert not EquipmentReservation.objects.exists()

    def it_refuses_a_free_road_on_an_item_that_turned_hourly_after_the_read():
        stale = _tool()
        Equipment.objects.filter(pk=stale.pk).update(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500)
        with pytest.raises(EquipmentError, match="price for this time just changed"):
            equipment_service.reserve(stale, _member(), _at(_day(), 14), 60)
        assert not EquipmentReservation.objects.exists()


def describe_start_reservation_checkout():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_holds_the_time_silently_and_opens_checkout(mock_create):
        equipment = _hourly(name="Sprocketmoor Router")
        member = _member()
        mail.outbox.clear()

        url = equipment_service.start_reservation_checkout(equipment, member, _at(_day(), 14), 90, purpose=" Signs ")

        assert url == _SESSION["url"]
        hold = EquipmentReservation.objects.get(member=member)
        assert hold.status == HOLD
        assert hold.amount_paid_cents == 0
        assert hold.stripe_session_id == "cs_res_1"
        assert hold.purpose == "Signs"
        assert hold.ends_at == _at(_day(), 15, 30)
        assert mail.outbox == []
        kwargs = mock_create.call_args.kwargs
        assert kwargs["amount_cents"] == 3750
        assert kwargs["metadata"] == {"kind": "equipment_reservation", "reservation_id": str(hold.pk)}
        assert kwargs["idempotency_key"] == f"reservation-checkout-{hold.pk}"
        assert kwargs["product_name"].startswith("Sprocketmoor Router reservation, ")
        assert kwargs["customer_email"] == "brindle@example.com"
        assert f"/equipment/{equipment.slug}/checkout/" in kwargs["success_url"]
        assert kwargs["cancel_url"].endswith("/cancelled/")
        lifetime = kwargs["expires_at"] - timezone.now().timestamp()
        assert 3500 < lifetime <= 3600

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_charges_a_donation_the_amount_entered(mock_create):
        equipment = _tool(pricing=Equipment.Pricing.DONATION)
        equipment_service.start_reservation_checkout(equipment, _member(), _at(_day(), 14), 60, amount_cents=1000)
        assert mock_create.call_args.kwargs["amount_cents"] == 1000

    @patch("billing.stripe_utils.create_checkout_session")
    def it_never_reaches_stripe_for_a_free_amount_or_a_lost_race(mock_create):
        donation = _tool(pricing=Equipment.Pricing.DONATION)
        with pytest.raises(EquipmentError, match="doesn't charge"):
            equipment_service.start_reservation_checkout(donation, _member(), _at(_day(), 14), 60, amount_cents=0)
        hourly = _hourly()
        EquipmentReservationFactory(equipment=hourly, starts_at=_at(_day(), 14), ends_at=_at(_day(), 15))
        with pytest.raises(EquipmentError, match="just taken"):
            equipment_service.start_reservation_checkout(hourly, _member("racer"), _at(_day(), 14), 60)
        mock_create.assert_not_called()
        assert not EquipmentReservation.objects.filter(status=HOLD).exists()

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_deletes_the_hold_when_stripe_fails(mock_create):
        with pytest.raises(RuntimeError):
            equipment_service.start_reservation_checkout(_hourly(), _member(), _at(_day(), 14), 60)
        assert not EquipmentReservation.objects.exists()

    def it_round_trips_its_token():
        hold = _hold()
        token = equipment_service.make_checkout_token(hold)
        assert equipment_service.read_checkout_token(token) == hold


def describe_finalize_paid_reservation():
    def it_confirms_once_and_sends_the_confirmation_and_the_managers_ping():
        hold = _hold(_hourly(name="Thimbleforge Saw"))
        mail.outbox.clear()

        first = equipment_service.finalize_paid_reservation(hold, payment_intent="pi_1", amount_total=3750)
        second = equipment_service.finalize_paid_reservation(hold, payment_intent="pi_1", amount_total=3750)

        assert (first, second) == ("finalized", "already")
        hold.refresh_from_db()
        assert hold.status == EquipmentReservation.Status.CONFIRMED
        assert (hold.amount_paid_cents, hold.stripe_payment_id) == (3750, "pi_1")
        assert [m.subject for m in mail.outbox if m.subject.startswith("Reserved")] != []
        assert len([m for m in mail.outbox if m.subject.startswith("Reserved")]) == 1

    @respx.mock
    def it_lands_awaiting_approval_with_only_the_request_emails_until_a_manager_approves():
        config = SiteConfiguration.load()
        config.discord_reservations_webhook_url = _RESERVATIONS_WEBHOOK
        config.save()
        route = respx.post(_RESERVATIONS_WEBHOOK).mock(return_value=httpx.Response(204))
        equipment = _hourly(requires_approval=True, name="Quillfeather Mill")
        manager = _member("approver")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        hold = _hold(equipment)
        mail.outbox.clear()

        assert (
            equipment_service.finalize_paid_reservation(hold, payment_intent="pi_2", amount_total=5000) == "finalized"
        )

        hold.refresh_from_db()
        assert hold.status == EquipmentReservation.Status.PENDING_APPROVAL
        subjects = [m.subject for m in mail.outbox]
        assert any(subject.startswith("Requested:") for subject in subjects)
        assert any(subject.startswith("Needs approval:") for subject in subjects)
        # A request is not booked yet: no confirmation, no #reservations post, no managers' booking bell.
        assert not any(subject.startswith("Reserved") for subject in subjects)
        assert not route.called
        assert not Notification.objects.filter(trigger="equipment.reservation_made").exists()

        mail.outbox.clear()
        hold.approve(manager)

        assert [m.subject for m in mail.outbox if m.subject.startswith("Reserved")] != []
        assert len([m for m in mail.outbox if m.subject.startswith("Reserved")]) == 1
        assert route.call_count == 1
        assert "Quillfeather Mill" in json.loads(route.calls[0].request.content)["embeds"][0]["description"]

    def it_confirms_a_managers_own_paid_hold_at_once():
        equipment = _hourly(requires_approval=True)
        manager = _member("selfmgr")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        hold = _hold(equipment, member=manager)
        equipment_service.finalize_paid_reservation(hold, payment_intent="pi_3", amount_total=None, session_id="cs_x")
        hold.refresh_from_db()
        assert hold.status == EquipmentReservation.Status.CONFIRMED
        assert hold.amount_paid_cents == 0  # no amount_total given, nothing stamped
        assert hold.stripe_session_id == "cs_res_1"  # an existing session id is never overwritten

    def it_backfills_a_missing_session_id_and_says_gone_for_a_released_hold():
        hold = _hold(stripe_session_id="")
        equipment_service.finalize_paid_reservation(hold, payment_intent="pi_4", amount_total=100, session_id="cs_new")
        hold.refresh_from_db()
        assert hold.stripe_session_id == "cs_new"
        gone = _hold(_hourly(name="Vanished Lathe"), member=_member("vanisher"))
        EquipmentReservation.objects.filter(pk=gone.pk).delete()
        assert equipment_service.finalize_paid_reservation(gone, payment_intent="pi", amount_total=1) == "gone"

    def it_locks_only_the_reservation_row():
        assert equipment_service.locked_reservation_queryset().query.select_for_update_of == ("self",)


def describe_release_and_resume():
    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved())
    def it_releases_an_unpaid_hold_and_frees_its_time(mock_retrieve, mock_expire):
        hold = _hold()
        assert equipment_service.release_hold_if_unpaid(hold) == "released"
        assert not EquipmentReservation.objects.filter(pk=hold.pk).exists()
        mock_expire.assert_called_once_with(session_id="cs_res_1")

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_paid_session())
    def it_keeps_and_finalizes_a_paid_hold(mock_retrieve):
        hold = _hold()
        assert equipment_service.release_hold_if_unpaid(hold) == "paid"
        hold.refresh_from_db()
        assert hold.status == EquipmentReservation.Status.CONFIRMED

    @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("down"))
    def it_keeps_the_hold_when_stripe_is_unreachable(mock_retrieve):
        hold = _hold()
        assert equipment_service.release_hold_if_unpaid(hold) == "unknown"
        assert EquipmentReservation.objects.filter(pk=hold.pk, status=HOLD).exists()

    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_releases_a_hold_with_no_session_outright(mock_retrieve):
        hold = _hold(stripe_session_id="")
        assert equipment_service.release_hold_if_unpaid(hold) == "released"
        mock_retrieve.assert_not_called()

    @patch("billing.stripe_utils.expire_checkout_session", side_effect=RuntimeError("already expired"))
    def it_releases_even_when_expiring_the_session_fails(mock_expire):
        hold = _hold()
        equipment_service._delete_hold(hold)
        assert not EquipmentReservation.objects.filter(pk=hold.pk).exists()

    @patch("billing.stripe_utils.expire_checkout_session")
    def it_has_no_session_to_expire_on_a_hold_that_never_got_one(mock_expire):
        hold = _hold(stripe_session_id="")
        equipment_service._delete_hold(hold)
        mock_expire.assert_not_called()
        assert not EquipmentReservation.objects.filter(pk=hold.pk).exists()

    def it_never_deletes_a_row_that_was_finalized_meanwhile():
        row = _paid_row()
        equipment_service._delete_hold(row, expire_session=False)
        assert EquipmentReservation.objects.filter(pk=row.pk).exists()

    def describe_reconcile_landed_checkout():
        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_paid_session())
        def it_finalizes_a_paid_session(mock_retrieve):
            assert equipment_service.reconcile_landed_checkout(_hold()) == "finalized"

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved())
        def it_waits_on_an_unpaid_one(mock_retrieve):
            assert equipment_service.reconcile_landed_checkout(_hold()) == "pending"

        def it_waits_with_no_session_to_ask_about():
            assert equipment_service.reconcile_landed_checkout(_hold(stripe_session_id="")) == "pending"

        @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("down"))
        def it_says_unknown_when_stripe_is_down(mock_retrieve):
            assert equipment_service.reconcile_landed_checkout(_hold()) == "unknown"

    def describe_resume_checkout():
        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved())
        def it_hands_back_the_same_open_session(mock_retrieve):
            assert equipment_service.resume_checkout(_hold()) == ("open", _SESSION["url"])

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_paid_session())
        def it_finalizes_an_already_paid_one(mock_retrieve):
            hold = _hold()
            assert equipment_service.resume_checkout(hold) == ("paid", "")
            hold.refresh_from_db()
            assert hold.status == EquipmentReservation.Status.CONFIRMED

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
        def it_releases_an_expired_one(mock_retrieve):
            hold = _hold()
            assert equipment_service.resume_checkout(hold) == ("released", "")
            assert not EquipmentReservation.objects.filter(pk=hold.pk).exists()

        @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(url=None))
        def it_releases_an_open_one_with_no_url(mock_retrieve):
            assert equipment_service.resume_checkout(_hold()) == ("released", "")

        def it_releases_a_hold_with_no_session():
            assert equipment_service.resume_checkout(_hold(stripe_session_id="")) == ("released", "")

        @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("down"))
        def it_says_unknown_when_stripe_is_down(mock_retrieve):
            assert equipment_service.resume_checkout(_hold()) == ("unknown", "")


def describe_expire_payment_holds():
    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_releases_unpaid_holds_recovers_paid_ones_and_leaves_young_ones(mock_retrieve, mock_expire):
        equipment = _hourly()
        unpaid = _hold(equipment, member=_member("unpaid"), age_hours=3, stripe_session_id="cs_unpaid")
        paid = EquipmentReservationFactory(
            equipment=equipment,
            member=_member("paidlate"),
            starts_at=_at(_day(), 13),
            ends_at=_at(_day(), 14),
            status=HOLD,
            stripe_session_id="cs_paid",
        )
        EquipmentReservation.objects.filter(pk=paid.pk).update(created_at=timezone.now() - timedelta(hours=3))
        young = _hold(_hourly(name="Young Press"), member=_member("young"), stripe_session_id="cs_young")
        sessionless = _hold(_hourly(name="Lost Press"), member=_member("lost"), age_hours=3, stripe_session_id="")
        mock_retrieve.side_effect = lambda session_id: (
            _paid_session() if session_id == "cs_paid" else _retrieved(id=session_id, status="expired")
        )

        released, recovered = equipment_service.expire_payment_holds()

        assert (released, recovered) == (2, 1)
        assert not EquipmentReservation.objects.filter(pk__in=[unpaid.pk, sessionless.pk]).exists()
        assert EquipmentReservation.objects.get(pk=paid.pk).status == EquipmentReservation.Status.CONFIRMED
        assert EquipmentReservation.objects.get(pk=young.pk).status == HOLD

    @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("down"))
    def it_skips_a_hold_stripe_cannot_answer_for(mock_retrieve):
        hold = _hold(age_hours=3)
        assert equipment_service.expire_payment_holds() == (0, 0)
        assert EquipmentReservation.objects.filter(pk=hold.pk, status=HOLD).exists()

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_paid_session())
    def it_counts_nothing_when_a_webhook_won_the_race(mock_retrieve):
        hold = _hold(age_hours=3)
        with patch.object(equipment_service, "finalize_paid_reservation", return_value="already"):
            assert equipment_service.expire_payment_holds() == (0, 0)
        assert EquipmentReservation.objects.filter(pk=hold.pk).exists()

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
    @patch("billing.stripe_utils.expire_checkout_session")
    def it_runs_as_the_release_reservation_payment_holds_command(mock_expire, mock_retrieve, capsys):
        _hold(age_hours=3)
        call_command("release_reservation_payment_holds")
        assert "Released 1 hold(s); recovered 0 paid reservation(s)." in capsys.readouterr().out

    def it_is_registered_as_a_fifteen_minute_job():
        from core.scheduled_jobs import JOBS_BY_KEY, Cadence

        job = JOBS_BY_KEY["release_reservation_payment_holds"]
        assert (job.command, job.cadence, job.money_job) == ("release_reservation_payment_holds", Cadence.ALWAYS, False)


def describe_webhooks():
    def describe_completed():
        def it_finalizes_the_hold_named_in_the_metadata():
            hold = _hold()
            webhook_handlers.handle_reservation_checkout_completed(_event(reservation_id=str(hold.pk)))
            hold.refresh_from_db()
            assert hold.status == EquipmentReservation.Status.CONFIRMED
            assert (hold.amount_paid_cents, hold.stripe_payment_id) == (3750, "pi_res_1")

        def it_ignores_other_kinds_missing_ids_and_unpaid_sessions():
            hold = _hold()
            other = _event(reservation_id=str(hold.pk))
            other["data"]["object"]["metadata"]["kind"] = "orientation_booking"
            webhook_handlers.handle_reservation_checkout_completed(other)
            webhook_handlers.handle_reservation_checkout_completed(_event())
            unpaid = _event(reservation_id=str(hold.pk))
            unpaid["data"]["object"]["payment_status"] = "unpaid"
            webhook_handlers.handle_reservation_checkout_completed(unpaid)
            no_metadata = {"data": {"object": {"id": "cs_x", "metadata": None}}}
            webhook_handlers.handle_reservation_checkout_completed(no_metadata)
            hold.refresh_from_db()
            assert hold.status == HOLD

        def it_takes_an_amount_that_is_not_a_number_as_no_amount():
            hold = _hold()
            event = _event(reservation_id=str(hold.pk))
            event["data"]["object"]["amount_total"] = None
            webhook_handlers.handle_reservation_checkout_completed(event)
            hold.refresh_from_db()
            assert (hold.status, hold.amount_paid_cents) == (EquipmentReservation.Status.CONFIRMED, 0)

        def it_alerts_the_billing_administrators_about_a_payment_with_no_reservation():
            with patch.object(webhook_handlers, "_send_orphan_payment_alert") as alert:
                webhook_handlers.handle_reservation_checkout_completed(_event(reservation_id="999999"))
            assert alert.call_args.kwargs == {"reason": "Reservation 999999 no longer exists.", "item": "reservation"}

        def it_alerts_when_the_reservation_vanishes_mid_finalize():
            hold = _hold()
            with (
                patch.object(equipment_service, "finalize_paid_reservation", return_value="gone"),
                patch.object(webhook_handlers, "_send_orphan_payment_alert") as alert,
            ):
                webhook_handlers.handle_reservation_checkout_completed(_event(reservation_id=str(hold.pk)))
            assert "released while the payment landed" in alert.call_args.kwargs["reason"]

        def it_stays_quiet_on_a_redelivery_but_alerts_on_a_row_that_moved_on_unpaid():
            paid = _paid_row()
            with patch.object(webhook_handlers, "_send_orphan_payment_alert") as alert:
                webhook_handlers.handle_reservation_checkout_completed(_event(reservation_id=str(paid.pk)))
            alert.assert_not_called()
            free = EquipmentReservationFactory(equipment=_hourly(name="Free Mill"), member=_member("freemill"))
            with patch.object(webhook_handlers, "_send_orphan_payment_alert") as alert:
                webhook_handlers.handle_reservation_checkout_completed(_event(reservation_id=str(free.pk)))
            assert "without ever recording a payment" in alert.call_args.kwargs["reason"]

        def it_sends_the_orphan_alert_worded_for_a_reservation():
            with patch("core.events.senders.emit_flat_email") as emit:
                webhook_handlers.handle_reservation_checkout_completed(_event(reservation_id="424242"))
            kwargs = emit.call_args.kwargs
            assert kwargs["subject"] == "Orphaned reservation payment needs a manual refund"
            assert kwargs["text_body"].startswith("A paid reservation Checkout landed with no reservation to credit.")

        def it_reaches_the_handler_through_the_billing_fan_in():
            from billing import views as billing_views

            hold = _hold()
            billing_views._dispatch_checkout_completed(_event(reservation_id=str(hold.pk)))
            hold.refresh_from_db()
            assert hold.status == EquipmentReservation.Status.CONFIRMED

    def describe_expired():
        def it_releases_the_hold_without_asking_stripe_again():
            hold = _hold()
            with patch("billing.stripe_utils.expire_checkout_session") as expire:
                webhook_handlers.handle_reservation_checkout_expired(_event(reservation_id=str(hold.pk)))
            expire.assert_not_called()
            assert not EquipmentReservation.objects.filter(pk=hold.pk).exists()

        def it_ignores_other_kinds_missing_ids_and_rows_no_longer_held():
            row = _paid_row()
            webhook_handlers.handle_reservation_checkout_expired(_event(reservation_id=str(row.pk)))
            webhook_handlers.handle_reservation_checkout_expired(_event())
            other = _event(reservation_id=str(row.pk))
            other["data"]["object"]["metadata"] = None
            webhook_handlers.handle_reservation_checkout_expired(other)
            assert EquipmentReservation.objects.filter(pk=row.pk).exists()

        def it_reaches_the_handler_through_the_billing_fan_in():
            from billing import views as billing_views

            hold = _hold()
            billing_views._dispatch_checkout_expired(_event(reservation_id=str(hold.pk)))
            assert not EquipmentReservation.objects.filter(pk=hold.pk).exists()


def describe_automatic_refunds():
    def _with_manager(equipment: Equipment, username: str = "decider") -> Member:
        manager = _member(username)
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        return manager

    @patch("billing.stripe_utils.create_refund", return_value=_refund_result())
    def it_refunds_a_paid_request_in_full_on_decline_and_says_so_in_the_email(mock_refund):
        equipment = _hourly(requires_approval=True, name="Quillmoss Engraver")
        manager = _with_manager(equipment)
        row = _paid_row(equipment, status=EquipmentReservation.Status.PENDING_APPROVAL)
        mail.outbox.clear()

        row.decline(manager, reason="The bed is warped")

        assert row.refund_outcome == "refunded"
        assert mock_refund.call_args.kwargs["amount_cents"] == 5000
        declined = next(m for m in mail.outbox if "declined" in m.subject)
        assert "Your $50.00 has been refunded to your card. It can take 5 to 10 days to show." in declined.body
        assert row.refund_state == "full"

    @patch("billing.stripe_utils.create_refund", side_effect=stripe.StripeError("The charge is disputed."))
    def it_still_declines_when_stripe_refuses_the_refund(mock_refund):
        equipment = _hourly(requires_approval=True)
        manager = _with_manager(equipment)
        row = _paid_row(equipment, status=EquipmentReservation.Status.PENDING_APPROVAL)
        mail.outbox.clear()

        row.decline(manager, reason="Booked for repair")

        row = EquipmentReservation.objects.get(pk=row.pk)
        assert row.status == EquipmentReservation.Status.DECLINED
        assert row.refund_state == "failed"
        declined = next(m for m in mail.outbox if "declined" in m.subject)
        assert "Your $50.00 refund is being processed." in declined.body

    @patch("billing.stripe_utils.create_refund", return_value=_refund_result())
    def it_refunds_on_a_manager_cancel(mock_refund):
        equipment = _hourly()
        manager = _with_manager(equipment)
        row = _paid_row(equipment)
        mail.outbox.clear()

        row.cancel(manager, reason="Spindle broke", as_manager=True)

        assert row.refund_outcome == "refunded"
        cancelled = next(m for m in mail.outbox if "was cancelled" in m.subject)
        assert "Your $50.00 has been refunded to your card." in cancelled.body

    @patch("billing.stripe_utils.create_refund", return_value=_refund_result())
    def it_refunds_on_a_member_cancel(mock_refund):
        row = _paid_row(
            starts_at=timezone.now() + timedelta(days=3), ends_at=timezone.now() + timedelta(days=3, hours=1)
        )
        mail.outbox.clear()

        fee = row.cancel(row.member)

        assert fee is None
        assert row.refund_outcome == "refunded"
        confirmation = next(m for m in mail.outbox if "You cancelled" in m.subject)
        assert "Your $50.00 has been refunded to your card." in confirmation.body

    @patch("billing.stripe_utils.create_refund", return_value=_refund_result())
    def it_refunds_a_late_self_cancel_in_full_and_still_charges_the_late_fee_separately(mock_refund):
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.save()
        equipment = _hourly(late_cancel_fee_cents=1000)
        soon = timezone.now() + timedelta(hours=2)
        row = _paid_row(equipment, starts_at=soon, ends_at=soon + timedelta(hours=1))

        fee = row.cancel(row.member)

        assert fee is not None
        assert fee.amount_cents == 1000
        assert fee.status == LateCancellationFee.Status.UNPAID
        assert mock_refund.call_args.kwargs["amount_cents"] == 5000  # never netted against the fee
        assert row.refund_outcome == "refunded"

    @patch("billing.stripe_utils.create_refund")
    def it_never_refunds_a_free_or_already_refunded_row(mock_refund):
        free = EquipmentReservationFactory(equipment=_hourly(), member=_member("freebie"))
        assert equipment_service.refund_if_paid(free, actor=None) == ""
        refunded = _paid_row()
        PaymentRefund.objects.create(reservation=refunded, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
        assert equipment_service.refund_if_paid(refunded, actor=None) == ""
        mock_refund.assert_not_called()

    def it_writes_no_refund_line_when_nothing_was_refunded():
        assert equipment_service.refund_line(EquipmentReservationFactory()) == ""
