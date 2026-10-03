"""BDD specs for ``orientations.cancel_times`` (issue #574): a mixed selection of fixed slots and
open windows, what is skipped (a slot carved out of a live window, a foreign pk, a cancelled or
past time), and the cancelled and emailed counts."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from membership import orientations
from membership.models import Guild, OrientationBooking, OrientationSlot
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityBlockFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _guild() -> Guild:
    MembershipPlanFactory()
    guild = GuildFactory()
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    OrientationTypeFactory(guild=guild, name="Press Orientation", duration_minutes=60)
    return guild


def _slot(guild: Guild, *, days: int = 2) -> OrientationSlot:
    start = timezone.now() + timedelta(days=days)
    return OrientationSlotFactory(
        guild=guild,
        orientation_type=guild.orientation_types.first(),
        starts_at=start,
        ends_at=start + timedelta(hours=1),
    )


def describe_cancel_times():
    def it_cancels_slots_through_cancel_slot_and_windows_through_cancel():
        guild = _guild()
        booked, empty = _slot(guild), _slot(guild, days=3)
        OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.CONFIRMED)
        OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.REQUESTED)
        OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.CANCELLED)  # not active, not counted
        window = OrientationAvailabilityBlockFactory(guild=guild)
        with patch("membership.orientations.cancel_slot") as cancel_slot:
            result = orientations.cancel_times(guild, [booked.pk, empty.pk], [window.pk])
        assert result == (3, 2)
        assert sorted(call.args[0].pk for call in cancel_slot.call_args_list) == sorted([booked.pk, empty.pk])
        window.refresh_from_db()
        assert window.is_cancelled is True

    def it_really_cancels_the_slot_and_its_booking():
        guild = _guild()
        slot = _slot(guild)
        booking = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        assert orientations.cancel_times(guild, [slot.pk], []) == (1, 1)
        slot.refresh_from_db()
        booking.refresh_from_db()
        assert slot.is_cancelled is True
        assert booking.status == OrientationBooking.Status.CANCELLED

    def it_skips_a_slot_carved_out_of_a_live_window():
        guild = _guild()
        later = timezone.now() + timedelta(days=5)
        window = OrientationAvailabilityBlockFactory(guild=guild, starts_at=later, ends_at=later + timedelta(hours=3))
        booking = orientations.request_block_orientation(
            window, MemberFactory(), later + timedelta(hours=1), orientation_type=guild.orientation_types.first()
        )
        assert booking.slot.source == OrientationSlot.Source.FROM_BLOCK
        assert orientations.cancel_times(guild, [booking.slot.pk], []) == (0, 0)
        booking.slot.refresh_from_db()
        assert booking.slot.is_cancelled is False

    def it_cancels_a_carved_slot_once_its_window_is_cancelled():
        guild = _guild()
        later = timezone.now() + timedelta(days=5)
        window = OrientationAvailabilityBlockFactory(guild=guild, starts_at=later, ends_at=later + timedelta(hours=3))
        booking = orientations.request_block_orientation(
            window, MemberFactory(), later + timedelta(hours=1), orientation_type=guild.orientation_types.first()
        )
        window.cancel()  # the segment now stands on its own row on the card
        assert orientations.cancel_times(guild, [booking.slot.pk], []) == (1, 1)
        booking.slot.refresh_from_db()
        assert booking.slot.is_cancelled is True

    def it_skips_a_foreign_pk_a_cancelled_time_and_a_past_one():
        guild = _guild()
        mine = _slot(guild)
        other = _guild()
        foreign_slot = _slot(other)
        foreign_window = OrientationAvailabilityBlockFactory(guild=other)
        gone = _slot(guild, days=4)
        gone.mark_cancelled()
        past = _slot(guild, days=-3)
        with patch("membership.orientations.cancel_slot") as cancel_slot:
            result = orientations.cancel_times(
                guild, [mine.pk, foreign_slot.pk, gone.pk, past.pk, 9999999], [foreign_window.pk, 9999999]
            )
        assert result == (1, 0)
        assert [call.args[0].pk for call in cancel_slot.call_args_list] == [mine.pk]
        foreign_window.refresh_from_db()
        assert foreign_window.is_cancelled is False

    def it_returns_zeros_for_an_empty_selection():
        guild = _guild()
        _slot(guild)
        with patch("membership.orientations.cancel_slot") as cancel_slot:
            assert orientations.cancel_times(guild, [], []) == (0, 0)
        cancel_slot.assert_not_called()
