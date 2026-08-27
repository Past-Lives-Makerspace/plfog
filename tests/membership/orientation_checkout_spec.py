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
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_test_1", "url": "https://checkout.stripe.example/cs_test_1"}


def _paid_slot(price_cents: int = 1500, **slot_kwargs) -> OrientationSlot:
    settings_obj = GuildOrientationSettingsFactory(price_cents=price_cents)
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
        assert hold.amount_paid_cents == 1500
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
        assert kwargs["product_name"] == f"Orientation — {slot.guild.name}"
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
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500, allow_custom_requests=True)
        starts = timezone.now() + timedelta(days=3)
        url = orientations.start_custom_orientation_checkout(settings_obj.guild, MemberFactory(), starts)
        assert url == _SESSION["url"]
        slot = OrientationSlot.objects.get(guild=settings_obj.guild)
        assert slot.seats == 1
        assert slot.source == OrientationSlot.Source.MANUAL

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_cleans_up_the_orphan_slot_on_failure(mock_create):
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500, allow_custom_requests=True)
        with pytest.raises(RuntimeError):
            orientations.start_custom_orientation_checkout(
                settings_obj.guild, MemberFactory(), timezone.now() + timedelta(days=3)
            )
        assert OrientationSlot.objects.filter(guild=settings_obj.guild).count() == 0

    def it_refuses_when_custom_requests_are_off():
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500, allow_custom_requests=False)
        with pytest.raises(OrientationError):
            orientations.start_custom_orientation_checkout(
                settings_obj.guild, MemberFactory(), timezone.now() + timedelta(days=3)
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
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500)
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
    def it_keeps_the_provisional_amount_when_the_session_has_none():
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500
        )
        orientations.finalize_paid_booking(hold, payment_intent="pi_1", amount_total=None)
        hold.refresh_from_db()
        assert hold.amount_paid_cents == 1500
        assert hold.status == OrientationBooking.Status.REQUESTED
