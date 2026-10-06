"""BDD specs for donation based orientation types (#636): the amount rule, the labels and the checkout roads."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from billing.models import PaymentRefund
from membership import orientations
from membership.models import OrientationBooking, OrientationError, OrientationSlot, OrientationType
from tests.membership.factories import (
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationAvailabilityBlockFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_donation_1", "url": "https://checkout.stripe.example/cs_donation_1"}


def _donation_type(*, minimum: int = 0, suggested: int | None = None, **fields: object) -> OrientationType:
    settings_obj = GuildOrientationSettingsFactory(allow_custom_requests=True)
    fields.setdefault("name", "Jewelry Basics")
    return OrientationTypeFactory(
        guild=settings_obj.guild,
        is_donation=True,
        donation_minimum_cents=minimum,
        donation_suggested_cents=suggested,
        **fields,
    )


def _slot_for(orientation_type: OrientationType) -> OrientationSlot:
    return OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)


def describe_checkout_amount_cents():
    def it_charges_a_fixed_type_its_price_whatever_the_member_sent():
        orientation_type = OrientationTypeFactory(price_cents=1500)
        assert orientation_type.checkout_amount_cents(None) == 1500
        assert orientation_type.checkout_amount_cents(1) == 1500

    def it_charges_a_donation_type_exactly_what_the_member_entered():
        orientation_type = _donation_type()
        assert orientation_type.checkout_amount_cents(1234) == 1234
        assert orientation_type.checkout_amount_cents(100) == 100

    def it_takes_zero_as_a_free_booking_when_the_minimum_is_zero():
        assert _donation_type().checkout_amount_cents(0) == 0

    @pytest.mark.parametrize("cents", [1, 50, 99])
    def it_refuses_an_amount_between_one_cent_and_ninety_nine(cents: int):
        with pytest.raises(OrientationError, match=r"Any amount above \$0 has to be at least \$1\. Enter \$0"):
            _donation_type().checkout_amount_cents(cents)

    def it_refuses_an_amount_under_the_minimum_with_the_minimum_named():
        orientation_type = _donation_type(minimum=500)
        with pytest.raises(OrientationError, match=r"^The minimum for this orientation is \$5\.$"):
            orientation_type.checkout_amount_cents(499)
        with pytest.raises(OrientationError, match=r"^The minimum for this orientation is \$5\.$"):
            orientation_type.checkout_amount_cents(0)
        assert orientation_type.checkout_amount_cents(500) == 500

    def it_refuses_an_amount_over_five_hundred_dollars():
        orientation_type = _donation_type()
        assert orientation_type.checkout_amount_cents(50000) == 50000
        with pytest.raises(OrientationError, match=r"^Enter an amount up to \$500\.$"):
            orientation_type.checkout_amount_cents(50001)
        with pytest.raises(OrientationError, match=r"^Enter an amount up to \$500\.$"):
            orientation_type.checkout_amount_cents(9999999)

    def it_refuses_a_negative_amount():
        with pytest.raises(OrientationError, match=r"^Enter \$0 or more\.$"):
            _donation_type().checkout_amount_cents(-100)

    def it_asks_for_an_amount_when_none_was_sent():
        with pytest.raises(OrientationError, match=r"^Enter what you'd like to pay\. \$0 is fine\.$"):
            _donation_type().checkout_amount_cents(None)
        with pytest.raises(OrientationError, match=r"^Enter what you'd like to pay, \$12\.50 or more\.$"):
            _donation_type(minimum=1250).checkout_amount_cents(None)


def describe_is_paid():
    def it_is_false_for_a_donation_type_even_with_a_stored_price():
        assert _donation_type(price_cents=1500).is_paid is False

    def it_stays_true_for_a_fixed_price_type():
        assert OrientationTypeFactory(price_cents=1500).is_paid is True


def describe_labels():
    def it_shows_the_suggestion_on_the_card():
        assert _donation_type(suggested=1500).donation_label == "Donation, $15 suggested"
        assert _donation_type(suggested=1250, name="Other").donation_label == "Donation, $12.50 suggested"

    def it_says_pay_what_you_can_without_a_suggestion():
        assert _donation_type().donation_label == "Pay what you can"

    def it_gives_the_input_a_plain_number_to_start_from():
        assert _donation_type(suggested=1500).donation_suggested_dollars == "15"
        assert _donation_type(suggested=1250, name="Other").donation_suggested_dollars == "12.50"
        assert _donation_type(name="None").donation_suggested_dollars == ""

    def it_hints_the_floor_under_the_input():
        assert _donation_type().donation_amount_hint == (
            "$0 sends a free request. Any other amount is at least $1, paid by card when you book."
        )
        assert _donation_type(minimum=500, name="Min").donation_amount_hint == (
            "At least $5, paid by card when you book."
        )

    def it_formats_dollars_for_copy():
        assert OrientationType.dollars(0) == "$0"
        assert OrientationType.dollars(100) == "$1"
        assert OrientationType.dollars(1205) == "$12.05"


def describe_start_orientation_checkout_for_a_donation():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_sends_the_members_amount_to_stripe(mock_create):
        slot = _slot_for(_donation_type(suggested=1500))
        url = orientations.start_orientation_checkout(slot, MemberFactory(), amount_cents=1234)
        assert url == _SESSION["url"]
        assert mock_create.call_args.kwargs["amount_cents"] == 1234
        hold = OrientationBooking.objects.get(slot=slot)
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT
        assert mock_create.call_args.kwargs["idempotency_key"] == f"orientation-checkout-{hold.pk}"

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_an_amount_under_the_floor_before_any_hold(mock_create):
        slot = _slot_for(_donation_type())
        with pytest.raises(OrientationError, match="at least"):
            orientations.start_orientation_checkout(slot, MemberFactory(), amount_cents=50)
        mock_create.assert_not_called()
        assert not OrientationBooking.objects.filter(slot=slot).exists()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_an_amount_over_the_ceiling_before_any_hold(mock_create):
        slot = _slot_for(_donation_type())
        with pytest.raises(OrientationError, match=r"up to \$500"):
            orientations.start_orientation_checkout(slot, MemberFactory(), amount_cents=150000)
        mock_create.assert_not_called()
        assert not OrientationBooking.objects.filter(slot=slot).exists()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_a_zero_amount_as_nothing_to_charge(mock_create):
        slot = _slot_for(_donation_type())
        with pytest.raises(OrientationError, match="doesn't charge to book"):
            orientations.start_orientation_checkout(slot, MemberFactory(), amount_cents=0)
        mock_create.assert_not_called()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_passes_the_amount_through_the_custom_road(mock_create):
        orientation_type = _donation_type()
        starts = timezone.now() + timedelta(days=3)
        orientations.start_custom_orientation_checkout(
            orientation_type.guild, MemberFactory(), starts, orientation_type=orientation_type, amount_cents=2000
        )
        assert mock_create.call_args.kwargs["amount_cents"] == 2000

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_passes_the_amount_through_the_block_road(mock_create):
        orientation_type = _donation_type()
        block = OrientationAvailabilityBlockFactory(guild=orientation_type.guild)
        orientations.start_block_orientation_checkout(
            block, MemberFactory(), block.starts_at, orientation_type=orientation_type, amount_cents=700
        )
        assert mock_create.call_args.kwargs["amount_cents"] == 700


def describe_refunding_a_donation():
    @patch(
        "billing.stripe_utils.create_refund",
        return_value={"id": "re_donation_1", "status": "succeeded", "amount": 1234},
    )
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refunds_exactly_what_was_paid_on_a_decline(mock_create, mock_refund):
        slot = _slot_for(_donation_type(suggested=1500))
        orientations.start_orientation_checkout(slot, MemberFactory(), amount_cents=1234)
        hold = OrientationBooking.objects.get(slot=slot)
        orientations.finalize_paid_booking(hold, payment_intent="pi_donation_1", amount_total=1234)
        booking = OrientationBooking.objects.get(pk=hold.pk)
        assert booking.amount_paid_cents == 1234

        orientations.decline_orientation(booking)

        refund = PaymentRefund.objects.get()
        assert refund.amount_cents == 1234
        mock_refund.assert_called_once()

    @patch(
        "billing.stripe_utils.create_refund",
        return_value={"id": "re_donation_2", "status": "succeeded", "amount": 900},
    )
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refunds_exactly_what_was_paid_on_a_cancel(mock_create, mock_refund):
        member = MemberFactory()
        slot = _slot_for(_donation_type())
        orientations.start_orientation_checkout(slot, member, amount_cents=900)
        hold = OrientationBooking.objects.get(slot=slot)
        orientations.finalize_paid_booking(hold, payment_intent="pi_donation_2", amount_total=900)

        orientations.cancel_orientation(OrientationBooking.objects.get(pk=hold.pk), actor_label="the member")

        assert PaymentRefund.objects.get().amount_cents == 900
