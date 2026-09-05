"""BDD specs for checkout orchestration: start, Stripe-verified release, resume recovery, and the sweep."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone

from core.models import SiteActivity
from membership import orientations
from membership.models import OrientationBooking, OrientationError, OrientationSlot
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_test_1", "url": "https://checkout.stripe.example/cs_test_1"}


def _paid_slot(price_cents: int = 1500, **slot_kwargs) -> OrientationSlot:
    settings_obj = GuildOrientationSettingsFactory()
    OrientationTypeFactory(guild=settings_obj.guild, price_cents=price_cents)
    return OrientationSlotFactory(guild=settings_obj.guild, **slot_kwargs)


def _retrieved(**overrides):
    session = {
        "id": "cs_test_1",
        "url": "https://checkout.stripe.example/cs_test_1",
        "status": "open",
        "payment_status": "unpaid",
        "payment_intent": "",
        "amount_total": None,
    }
    session.update(overrides)
    return session


def describe_start_orientation_checkout():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_creates_a_silent_seat_hold_and_returns_the_checkout_url(mock_create):
        slot = _paid_slot()
        member = MemberFactory()
        mail.outbox.clear()

        url = orientations.start_orientation_checkout(slot, member, note="hi")

        assert url == _SESSION["url"]
        hold = OrientationBooking.objects.get(member=member)
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT
        # No provisional amount: money is stamped only at finalize, so a
        # never-paid hold can never render as a paid row anywhere.
        assert hold.amount_paid_cents == 0
        assert hold.stripe_session_id == "cs_test_1"
        assert hold.member_note == "hi"
        # Nothing has happened yet: no emails, no activity, no notifications.
        assert mail.outbox == []
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_REQUESTED).exists()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_sends_the_kind_metadata_and_per_booking_idempotency_key(mock_create):
        slot = _paid_slot()
        orientations.start_orientation_checkout(slot, MemberFactory())
        kwargs = mock_create.call_args.kwargs
        booking = OrientationBooking.objects.get()
        assert kwargs["metadata"] == {"kind": "orientation_booking", "booking_id": str(booking.pk)}
        assert kwargs["idempotency_key"] == f"orientation-checkout-{booking.pk}"
        assert kwargs["amount_cents"] == 1500
        assert kwargs["product_name"] == f"{slot.orientation_type.name} orientation — {slot.guild.name}"
        assert kwargs["expires_at"] is not None

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_rolls_back_the_hold_on_stripe_failure(mock_create):
        slot = _paid_slot()
        with pytest.raises(RuntimeError):
            orientations.start_orientation_checkout(slot, MemberFactory())
        assert OrientationBooking.objects.count() == 0

    def it_refuses_a_free_guild():
        slot = OrientationSlotFactory()
        with pytest.raises(OrientationError):
            orientations.start_orientation_checkout(slot, MemberFactory())

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_rejects_a_member_with_a_checkout_already_in_progress(mock_create):
        slot = _paid_slot()
        member = MemberFactory()
        orientations.start_orientation_checkout(slot, member)
        other = OrientationSlotFactory(guild=slot.guild)
        with pytest.raises(OrientationError, match="checkout in progress"):
            orientations.start_orientation_checkout(other, member)


def describe_start_custom_orientation_checkout():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_creates_a_one_seat_manual_slot_and_delegates(mock_create):
        settings_obj = GuildOrientationSettingsFactory(allow_custom_requests=True)
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild, price_cents=1500)
        starts = timezone.now() + timedelta(days=3)
        url = orientations.start_custom_orientation_checkout(
            settings_obj.guild, MemberFactory(), starts, orientation_type=orientation_type
        )
        assert url == _SESSION["url"]
        slot = OrientationSlot.objects.get(guild=settings_obj.guild)
        assert slot.seats == 1
        assert slot.source == OrientationSlot.Source.MANUAL

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_cleans_up_the_orphan_slot_on_failure(mock_create):
        settings_obj = GuildOrientationSettingsFactory(allow_custom_requests=True)
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild, price_cents=1500)
        with pytest.raises(RuntimeError):
            orientations.start_custom_orientation_checkout(
                settings_obj.guild,
                MemberFactory(),
                timezone.now() + timedelta(days=3),
                orientation_type=orientation_type,
            )
        assert OrientationSlot.objects.filter(guild=settings_obj.guild).count() == 0

    def it_refuses_when_custom_requests_are_off():
        settings_obj = GuildOrientationSettingsFactory(allow_custom_requests=False)
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild, price_cents=1500)
        with pytest.raises(OrientationError):
            orientations.start_custom_orientation_checkout(
                settings_obj.guild,
                MemberFactory(),
                timezone.now() + timedelta(days=3),
                orientation_type=orientation_type,
            )


def describe_release_hold_if_unpaid():
    @pytest.fixture
    def hold():
        slot = _paid_slot()
        return OrientationBookingFactory(
            slot=slot,
            status=OrientationBooking.Status.PENDING_PAYMENT,
            amount_paid_cents=1500,
            stripe_session_id="cs_test_1",
        )

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
    def it_deletes_a_stripe_confirmed_unpaid_hold(mock_retrieve, hold):
        assert orientations.release_hold_if_unpaid(hold) == "released"
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()

    @patch(
        "billing.stripe_utils.retrieve_checkout_session",
        return_value=_retrieved(status="complete", payment_status="paid", payment_intent="pi_9", amount_total=1600),
    )
    def it_flips_a_paid_hold_to_requested_with_the_full_fan_out(mock_retrieve, hold):
        mail.outbox.clear()
        assert orientations.release_hold_if_unpaid(hold) == "paid"
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.REQUESTED
        assert hold.stripe_payment_id == "pi_9"
        assert hold.amount_paid_cents == 1600  # the session's amount_total is canonical
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_REQUESTED).exists()
        assert any("request received" in m.subject.lower() for m in mail.outbox)

    @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_keeps_the_hold_when_stripe_is_unreachable(mock_retrieve, hold):
        assert orientations.release_hold_if_unpaid(hold) == "unknown"
        assert OrientationBooking.objects.filter(pk=hold.pk).exists()

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
    def it_deletes_the_orphan_custom_slot_with_the_hold(mock_retrieve):
        settings_obj = GuildOrientationSettingsFactory()
        OrientationTypeFactory(guild=settings_obj.guild, price_cents=1500)
        slot = OrientationSlotFactory(guild=settings_obj.guild, seats=1, source=OrientationSlot.Source.MANUAL)
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id="cs_x"
        )
        orientations.release_hold_if_unpaid(hold)
        assert not OrientationSlot.objects.filter(pk=slot.pk).exists()


def describe_expire_payment_holds():
    def _stale_hold(**overrides):
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot,
            status=OrientationBooking.Status.PENDING_PAYMENT,
            amount_paid_cents=1500,
            stripe_session_id="cs_test_1",
            **overrides,
        )
        OrientationBooking.objects.filter(pk=hold.pk).update(requested_at=timezone.now() - timedelta(hours=3))
        return hold

    @patch("billing.stripe_utils.retrieve_checkout_session")
    def it_skips_holds_younger_than_two_hours(mock_retrieve):
        slot = _paid_slot()
        OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id="cs_young"
        )
        assert orientations.expire_payment_holds() == (0, 0)
        mock_retrieve.assert_not_called()

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
    def it_releases_a_stripe_confirmed_expired_hold(mock_retrieve):
        hold = _stale_hold()
        assert orientations.expire_payment_holds() == (1, 0)
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()

    @patch(
        "billing.stripe_utils.retrieve_checkout_session",
        return_value=_retrieved(status="complete", payment_status="paid", payment_intent="pi_lost", amount_total=1500),
    )
    def it_recovers_a_paid_hold_whose_webhook_was_lost(mock_retrieve):
        hold = _stale_hold()
        mail.outbox.clear()
        assert orientations.expire_payment_holds() == (0, 1)
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.REQUESTED
        assert hold.stripe_payment_id == "pi_lost"
        assert any("request received" in m.subject.lower() for m in mail.outbox)

    @patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_skips_and_keeps_the_hold_when_stripe_errors(mock_retrieve):
        hold = _stale_hold()
        assert orientations.expire_payment_holds() == (0, 0)
        assert OrientationBooking.objects.filter(pk=hold.pk).exists()

    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
    def it_is_idempotent(mock_retrieve):
        _stale_hold()
        assert orientations.expire_payment_holds() == (1, 0)
        assert orientations.expire_payment_holds() == (0, 0)


def describe_finalize_paid_booking():
    def it_keeps_a_legacy_provisional_amount_when_the_session_has_none():
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500
        )
        assert orientations.finalize_paid_booking(hold, payment_intent="pi_1", amount_total=None) == "finalized"
        hold.refresh_from_db()
        assert hold.amount_paid_cents == 1500
        assert hold.status == OrientationBooking.Status.REQUESTED

    def it_backfills_a_missing_session_id():
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id=""
        )
        orientations.finalize_paid_booking(hold, payment_intent="pi_1", amount_total=1500, session_id="cs_back_1")
        hold.refresh_from_db()
        assert hold.stripe_session_id == "cs_back_1"

    def describe_race_safety():
        def it_finalizes_exactly_once_when_two_paths_race(db):
            # The sweep-vs-late-webhook case: both hold a stale PENDING_PAYMENT copy.
            slot = _paid_slot()
            hold = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT)
            stale_copy = OrientationBooking.objects.get(pk=hold.pk)

            first = orientations.finalize_paid_booking(hold, payment_intent="pi_1", amount_total=1500)
            rows_after_first = SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_REQUESTED).count()
            mail.outbox.clear()
            second = orientations.finalize_paid_booking(stale_copy, payment_intent="pi_1", amount_total=1500)

            assert (first, second) == ("finalized", "already")
            assert mail.outbox == []  # no second fan-out
            # No duplicate activity from the losing path (the baseline count per
            # request is the existing fan-out's own business).
            assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_REQUESTED).count() == rows_after_first

        def it_never_resurrects_a_cancelled_booking(db):
            slot = _paid_slot()
            booking = OrientationBookingFactory(
                slot=slot,
                status=OrientationBooking.Status.CANCELLED,
                amount_paid_cents=1500,
                stripe_payment_id="pi_done",
            )
            stale_copy = OrientationBooking.objects.get(pk=booking.pk)

            assert orientations.finalize_paid_booking(stale_copy, payment_intent="pi_done", amount_total=1500) == (
                "already"
            )
            booking.refresh_from_db()
            assert booking.status == OrientationBooking.Status.CANCELLED

        def it_no_ops_when_the_row_is_gone(db):
            slot = _paid_slot()
            hold = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT)
            stale_copy = OrientationBooking.objects.get(pk=hold.pk)
            hold.delete()

            assert orientations.finalize_paid_booking(stale_copy, payment_intent="pi_1", amount_total=1500) == "gone"

    def describe_cancelled_slot():
        @patch("billing.refunds.issue_refund")
        def it_finalizes_then_auto_cancels_and_refunds(mock_issue):
            slot = _paid_slot()
            hold = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT)
            slot.mark_cancelled(reason="closed")
            mail.outbox.clear()

            outcome = orientations.finalize_paid_booking(hold, payment_intent="pi_dead", amount_total=1500)

            assert outcome == "cancelled_slot"
            hold.refresh_from_db()
            assert hold.status == OrientationBooking.Status.CANCELLED
            assert hold.amount_paid_cents == 1500
            mock_issue.assert_called_once()
            subjects = [m.subject.lower() for m in mail.outbox]
            assert any("cancelled" in s for s in subjects)  # one honest email
            assert not any("request received" in s for s in subjects)  # no request fan-out


def describe_session_expiry_on_release():
    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
    def it_expires_the_session_when_a_hold_is_released(mock_retrieve, mock_expire):
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id="cs_open_1"
        )
        assert orientations.release_hold_if_unpaid(hold) == "released"
        mock_expire.assert_called_once_with(session_id="cs_open_1")

    @patch("billing.stripe_utils.expire_checkout_session", side_effect=RuntimeError("already expired"))
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="expired"))
    def it_swallows_expire_errors_and_still_releases(mock_retrieve, mock_expire):
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id="cs_open_2"
        )
        assert orientations.release_hold_if_unpaid(hold) == "released"
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()

    def it_releases_a_hold_with_no_session_id_outright():
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id=""
        )
        assert orientations.release_hold_if_unpaid(hold) == "released"
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()


def describe_sweep_stranded_holds():
    def it_deletes_a_past_cutoff_hold_with_an_empty_session_id():
        # Crash between session create and save: nothing to verify, seat must free.
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id=""
        )
        OrientationBooking.objects.filter(pk=hold.pk).update(requested_at=timezone.now() - timedelta(hours=3))
        assert orientations.expire_payment_holds() == (1, 0)
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()


def describe_cancel_slot_holds():
    @patch("billing.stripe_utils.expire_checkout_session")
    @patch("billing.stripe_utils.retrieve_checkout_session", return_value=_retrieved(status="open"))
    def it_expires_and_deletes_unpaid_holds_on_a_cancelled_slot(mock_retrieve, mock_expire):
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id="cs_slot_1"
        )
        orientations.cancel_slot(slot, reason="closed")
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()
        mock_expire.assert_called_once_with(session_id="cs_slot_1")
        slot.refresh_from_db()
        assert slot.is_cancelled is True

    @patch("billing.refunds.issue_refund")
    @patch(
        "billing.stripe_utils.retrieve_checkout_session",
        return_value=_retrieved(status="complete", payment_status="paid", payment_intent="pi_slot", amount_total=1500),
    )
    def it_routes_a_paid_hold_into_the_auto_cancel_refund_path(mock_retrieve, mock_issue):
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id="cs_slot_2"
        )
        orientations.cancel_slot(slot, reason="closed")
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.CANCELLED
        assert hold.stripe_payment_id == "pi_slot"
        mock_issue.assert_called_once()


def describe_equipment_owned_paid_flow():
    def _paid_equipment_slot(price_cents: int = 1500) -> OrientationSlot:
        from tests.membership.factories import EquipmentFactory

        equipment = EquipmentFactory(name="CNC Router")
        orientation_type = OrientationTypeFactory(
            equipment_owned=True, equipment=equipment, name="Operator Basics", price_cents=price_cents
        )
        return OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_names_the_equipment_on_the_stripe_product(mock_create):
        slot = _paid_equipment_slot()
        member = MemberFactory()
        url = orientations.start_orientation_checkout(slot, member)
        assert url == _SESSION["url"]
        assert mock_create.call_args.kwargs["product_name"] == "Operator Basics orientation — CNC Router"
        hold = OrientationBooking.objects.get(member=member)
        assert hold.guild is None
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_finalizes_and_fans_out_with_the_equipment_name(mock_create):
        slot = _paid_equipment_slot()
        member = MemberFactory()
        orientations.start_orientation_checkout(slot, member)
        hold = OrientationBooking.objects.get(member=member)
        mail.outbox.clear()
        orientations.finalize_paid_booking(hold, payment_intent="pi_equip_1", amount_total=1500)
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.REQUESTED
        member_email = next(m for m in mail.outbox if "request received" in m.subject)
        assert "CNC Router" in member_email.subject
        assert "[missing:" not in member_email.body


def describe_equipment_paid_checkout():
    """The equipment-owned paid path locks the Equipment row for the guard and the hold only (PR 2)."""

    def _equipment_paid_slot() -> OrientationSlot:
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, price_cents=1500)
        return OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)

    def it_commits_the_hold_before_the_stripe_call_and_holds_no_lock_across_it():
        from django.db import connection

        slot = _equipment_paid_slot()
        member = MemberFactory()
        seen: dict = {}

        def fake_create(**kwargs):
            seen["hold_exists"] = OrientationBooking.objects.filter(
                slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT
            ).exists()
            # Savepoint depth equal to the test's own baseline means the lock's
            # atomic block has already closed when Stripe is called.
            seen["depth"] = len(connection.savepoint_ids)
            return _SESSION

        baseline = len(connection.savepoint_ids)
        with patch("billing.stripe_utils.create_checkout_session", side_effect=fake_create):
            url = orientations.start_orientation_checkout(slot, member)
        assert url == _SESSION["url"]
        assert seen == {"hold_exists": True, "depth": baseline}
        assert OrientationBooking.objects.get(slot=slot).stripe_session_id == _SESSION["id"]

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_a_slot_under_a_confirmed_reservation_before_touching_stripe(mock_create):
        slot = _equipment_paid_slot()
        EquipmentReservationFactory(
            equipment=slot.orientation_type.equipment, starts_at=slot.starts_at, ends_at=slot.ends_at
        )
        with pytest.raises(OrientationError, match="not available to book"):
            orientations.start_orientation_checkout(slot, MemberFactory())
        mock_create.assert_not_called()
        assert not slot.bookings.exists()
