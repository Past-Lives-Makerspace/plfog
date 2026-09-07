"""BDD specs for orientation availability blocks (issue #283): the model, free intervals,
snap validation, and the carve-out booking paths (free and paid)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.utils import timezone

from membership import orientations
from membership.models import OrientationBooking, OrientationError, OrientationSlot
from tests.membership.factories import (
    MemberFactory,
    OrientationAvailabilityBlockFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_test_blk", "url": "https://checkout.stripe.example/cs_test_blk"}


def _block_with_type(*, duration_minutes: int = 60, price_cents: int = 0, **block_kwargs):
    block = OrientationAvailabilityBlockFactory(**block_kwargs)
    orientation_type = OrientationTypeFactory(
        guild=block.guild, duration_minutes=duration_minutes, price_cents=price_cents
    )
    return block, orientation_type


def describe_OrientationAvailabilityBlock():  # noqa: N802
    def describe_constraint():
        def it_rejects_a_block_that_ends_before_it_starts():
            start = timezone.now() + timedelta(days=2)
            with pytest.raises(IntegrityError):
                OrientationAvailabilityBlockFactory(starts_at=start, ends_at=start - timedelta(hours=1))

    def describe_free_intervals():
        def it_returns_the_whole_span_for_an_empty_block():
            block, _orientation_type = _block_with_type()
            assert block.free_intervals() == [(block.starts_at, block.ends_at)]

        def it_subtracts_a_booking_mid_block():
            block, orientation_type = _block_with_type()
            start = block.starts_at + timedelta(minutes=60)
            orientations.request_block_orientation(block, MemberFactory(), start, orientation_type=orientation_type)
            assert block.free_intervals() == [
                (block.starts_at, start),
                (start + timedelta(minutes=60), block.ends_at),
            ]

        def it_merges_back_to_back_bookings_into_one_busy_span():
            block, orientation_type = _block_with_type()
            first = block.starts_at + timedelta(minutes=60)
            second = block.starts_at + timedelta(minutes=120)
            orientations.request_block_orientation(block, MemberFactory(), first, orientation_type=orientation_type)
            other_type = OrientationTypeFactory(guild=block.guild, name="Second Type", duration_minutes=60)
            orientations.request_block_orientation(block, MemberFactory(), second, orientation_type=other_type)
            # first + second are adjacent 60-minute segments — one merged busy span remains.
            assert block.free_intervals() == [(block.starts_at, first)]

        @patch("billing.stripe_utils.expire_checkout_session")
        @patch("billing.stripe_utils.retrieve_checkout_session")
        @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
        def it_frees_the_segment_when_a_payment_hold_is_released(mock_create, mock_retrieve, mock_expire):
            block, orientation_type = _block_with_type(price_cents=1500)
            start = block.starts_at + timedelta(minutes=30)
            orientations.start_block_orientation_checkout(
                block, MemberFactory(), start, orientation_type=orientation_type
            )
            assert block.free_intervals() != [(block.starts_at, block.ends_at)]
            mock_retrieve.return_value = {"payment_status": "unpaid", "payment_intent": "", "amount_total": None}
            hold = OrientationBooking.objects.get(status=OrientationBooking.Status.PENDING_PAYMENT)
            assert orientations.release_hold_if_unpaid(hold) == "released"
            # The hold and its carved-out slot are gone — the segment is free again.
            assert block.free_intervals() == [(block.starts_at, block.ends_at)]
            assert not OrientationSlot.objects.filter(block=block).exists()

        def it_frees_the_segment_when_the_booking_is_cancelled():
            block, orientation_type = _block_with_type()
            start = block.starts_at + timedelta(minutes=60)
            booking = orientations.request_block_orientation(
                block, MemberFactory(), start, orientation_type=orientation_type
            )
            booking.cancel()
            assert block.free_intervals() == [(block.starts_at, block.ends_at)]

        def it_frees_the_segment_when_the_carved_slot_is_cancelled():
            block, orientation_type = _block_with_type()
            start = block.starts_at + timedelta(minutes=60)
            booking = orientations.request_block_orientation(
                block, MemberFactory(), start, orientation_type=orientation_type
            )
            orientations.cancel_slot(booking.slot)
            assert block.free_intervals() == [(block.starts_at, block.ends_at)]

    def describe_valid_starts_for():
        def it_lists_quarter_hour_starts_that_fit_the_duration():
            block, orientation_type = _block_with_type()  # 3-hour block, 60-minute type
            starts = block.valid_starts_for(orientation_type)
            assert starts[0] == block.starts_at
            assert starts[-1] == block.ends_at - timedelta(minutes=60)
            assert len(starts) == 9  # offsets 0..120 in 15-minute steps
            assert all((s - block.starts_at).total_seconds() % 900 == 0 for s in starts)

        def it_omits_starts_that_would_overlap_a_booking():
            block, orientation_type = _block_with_type()
            taken = block.starts_at + timedelta(minutes=60)
            orientations.request_block_orientation(block, MemberFactory(), taken, orientation_type=orientation_type)
            starts = block.valid_starts_for(orientation_type)
            assert block.starts_at in starts
            assert taken not in starts
            # A start 15 minutes before the booking would run into it — excluded too.
            assert taken - timedelta(minutes=15) not in starts
            assert taken + timedelta(minutes=60) in starts

        def it_returns_nothing_for_a_cancelled_block():
            block, orientation_type = _block_with_type()
            block.cancel()
            assert block.valid_starts_for(orientation_type) == []

        def it_omits_starts_already_in_the_past_for_a_running_block():
            now = timezone.now().replace(second=0, microsecond=0)
            block, orientation_type = _block_with_type(
                starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=2)
            )
            starts = block.valid_starts_for(orientation_type)
            assert starts  # the tail of the window is still offered
            assert all(s > timezone.now() for s in starts)

        def it_returns_nothing_when_the_type_outlasts_the_block():
            block, orientation_type = _block_with_type(duration_minutes=240)  # 4h type, 3h block
            assert block.valid_starts_for(orientation_type) == []

    def describe_dunder_str():
        def it_names_the_guild_window_and_orienter():
            block, _orientation_type = _block_with_type()
            text = str(block)
            assert block.guild.name in text
            assert "block" in text

    def describe_ensure_start_valid():
        def it_rejects_an_off_grid_start():
            block, orientation_type = _block_with_type()
            with pytest.raises(OrientationError, match="15 minute marks"):
                block.ensure_start_valid(orientation_type, block.starts_at + timedelta(minutes=7))

        def it_rejects_a_start_before_the_block_opens():
            block, orientation_type = _block_with_type()
            with pytest.raises(OrientationError, match="doesn't fit"):
                block.ensure_start_valid(orientation_type, block.starts_at - timedelta(minutes=30))

        def it_rejects_a_start_whose_end_passes_the_block_close():
            block, orientation_type = _block_with_type()
            with pytest.raises(OrientationError, match="doesn't fit"):
                block.ensure_start_valid(orientation_type, block.ends_at - timedelta(minutes=30))

        def it_rejects_a_start_already_in_the_past():
            block, orientation_type = _block_with_type(
                starts_at=timezone.now() - timedelta(hours=1), ends_at=timezone.now() + timedelta(hours=2)
            )
            with pytest.raises(OrientationError, match="already past"):
                block.ensure_start_valid(orientation_type, block.starts_at)

        def it_rejects_another_guilds_type():
            block, _orientation_type = _block_with_type()
            foreign_type = OrientationTypeFactory(name="Foreign")
            with pytest.raises(OrientationError, match="isn't offered"):
                block.ensure_start_valid(foreign_type, block.starts_at)

        def it_rejects_a_deactivated_type():
            block, orientation_type = _block_with_type()
            orientation_type.is_active = False
            orientation_type.save(update_fields=["is_active"])
            with pytest.raises(OrientationError, match="isn't offered"):
                block.ensure_start_valid(orientation_type, block.starts_at)


def describe_request_block_orientation():
    def it_carves_a_one_seat_from_block_slot_and_books_it():
        block, orientation_type = _block_with_type()
        member = MemberFactory()
        start = block.starts_at + timedelta(minutes=15)

        booking = orientations.request_block_orientation(block, member, start, orientation_type=orientation_type)

        assert booking.status == OrientationBooking.Status.REQUESTED
        slot = booking.slot
        assert slot.source == OrientationSlot.Source.FROM_BLOCK
        assert slot.block_id == block.pk
        assert slot.seats == 1
        assert slot.orienter_id == block.orienter_id
        assert slot.starts_at == start
        assert slot.ends_at == start + timedelta(minutes=orientation_type.duration_minutes)

    def it_fails_cleanly_when_a_second_attempt_overlaps():
        block, orientation_type = _block_with_type()
        start = block.starts_at + timedelta(minutes=60)
        orientations.request_block_orientation(block, MemberFactory(), start, orientation_type=orientation_type)

        # A partially overlapping start (15 minutes earlier) is rechecked and refused.
        with pytest.raises(OrientationError, match="just taken"):
            orientations.request_block_orientation(
                block, MemberFactory(), start - timedelta(minutes=15), orientation_type=orientation_type
            )
        assert OrientationSlot.objects.filter(block=block).count() == 1
        assert OrientationBooking.objects.count() == 1

    def it_rolls_back_the_carved_slot_when_the_booking_guards_refuse():
        block, orientation_type = _block_with_type()
        member = MemberFactory()
        orientations.request_block_orientation(block, member, block.starts_at, orientation_type=orientation_type)
        # Same member, same type: the per-type duplicate guard fires — and the second
        # carved slot must not survive the rollback.
        with pytest.raises(OrientationError, match="pending booking"):
            orientations.request_block_orientation(
                block, member, block.starts_at + timedelta(minutes=90), orientation_type=orientation_type
            )
        assert OrientationSlot.objects.filter(block=block).count() == 1

    def it_refuses_a_cancelled_block_but_leaves_existing_bookings_alone():
        block, orientation_type = _block_with_type()
        booking = orientations.request_block_orientation(
            block, MemberFactory(), block.starts_at, orientation_type=orientation_type
        )
        block.cancel()
        with pytest.raises(OrientationError, match="cancelled"):
            orientations.request_block_orientation(
                block, MemberFactory(), block.starts_at + timedelta(minutes=90), orientation_type=orientation_type
            )
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.slot.is_cancelled is False


def describe_start_block_orientation_checkout():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_creates_a_seat_holding_checkout_inside_the_block(mock_create):
        block, orientation_type = _block_with_type(price_cents=2500)
        member = MemberFactory()
        start = block.starts_at + timedelta(minutes=45)

        url = orientations.start_block_orientation_checkout(block, member, start, orientation_type=orientation_type)

        assert url == _SESSION["url"]
        hold = OrientationBooking.objects.get(member=member)
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT
        assert hold.slot.source == OrientationSlot.Source.FROM_BLOCK
        assert hold.slot.block_id == block.pk
        # The hold occupies its segment: that start is no longer offered.
        assert start not in block.valid_starts_for(orientation_type)

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_rolls_back_the_slot_and_hold_on_stripe_failure(mock_create):
        block, orientation_type = _block_with_type(price_cents=2500)
        with pytest.raises(RuntimeError):
            orientations.start_block_orientation_checkout(
                block, MemberFactory(), block.starts_at, orientation_type=orientation_type
            )
        assert not OrientationSlot.objects.filter(block=block).exists()
        assert not OrientationBooking.objects.exists()
