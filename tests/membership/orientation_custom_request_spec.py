"""BDD specs for the custom-orientation service glue: ``request_custom_orientation`` + ``parse_proposed_time``."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from membership import orientations
from membership.models import OrientationBooking, OrientationError, OrientationSlot
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _member(username: str = "custom_member") -> object:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com").member


def _future() -> datetime:
    return timezone.now() + timedelta(days=3)


def describe_request_custom_orientation():
    def it_creates_a_manual_single_seat_slot_and_requests_it():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(
            guild=guild,
            is_enabled=True,
            allow_custom_requests=True,
            default_duration_minutes=90,
            default_location="Front desk",
        )
        member = _member()
        starts = _future()

        booking = orientations.request_custom_orientation(guild, member, starts, note="please")

        assert booking.status == OrientationBooking.Status.REQUESTED
        slot = booking.slot
        assert slot.source == OrientationSlot.Source.MANUAL
        assert slot.seats == 1
        assert slot.location == "Front desk"
        assert slot.ends_at == starts + timedelta(minutes=90)
        assert booking.member_note == "please"

    def describe_when_the_guild_has_no_orientation_settings():
        def it_raises_and_creates_no_slot():
            guild = GuildFactory()
            member = _member()
            with pytest.raises(OrientationError, match="isn't taking custom orientation"):
                orientations.request_custom_orientation(guild, member, _future())
            assert not OrientationSlot.objects.filter(guild=guild).exists()

    def describe_when_the_guild_is_not_accepting():
        def it_raises_and_creates_no_slot():
            guild = GuildFactory()
            GuildOrientationSettingsFactory(guild=guild, is_enabled=True, is_closed=True)
            member = _member()
            with pytest.raises(OrientationError, match="isn't taking custom orientation"):
                orientations.request_custom_orientation(guild, member, _future())
            assert not OrientationSlot.objects.filter(guild=guild).exists()

    def describe_when_custom_requests_are_disallowed():
        def it_raises_and_creates_no_slot():
            guild = GuildFactory()
            GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=False)
            member = _member()
            with pytest.raises(OrientationError, match="isn't taking custom orientation"):
                orientations.request_custom_orientation(guild, member, _future())
            assert not OrientationSlot.objects.filter(guild=guild).exists()

    def describe_when_the_member_already_has_an_open_request():
        def it_deletes_the_orphan_slot_and_reraises():
            guild = GuildFactory()
            GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=True)
            member = _member()
            # An existing live booking makes slot.book reject the new custom slot.
            existing_slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
            OrientationBookingFactory(slot=existing_slot, member=member)
            before = OrientationSlot.objects.filter(guild=guild).count()

            with pytest.raises(OrientationError):
                orientations.request_custom_orientation(guild, member, _future())

            # The one-off MANUAL slot we tried to create was cleaned up (no net-new slot).
            assert OrientationSlot.objects.filter(guild=guild).count() == before

    def describe_when_the_member_is_already_oriented():
        def it_deletes_the_orphan_slot_and_reraises():
            guild = GuildFactory()
            GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=True)
            member = _member()
            done_slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
            OrientationBookingFactory(
                slot=done_slot, member=member, status=OrientationBooking.Status.CONFIRMED, is_completed=True
            )
            before = OrientationSlot.objects.filter(guild=guild).count()

            with pytest.raises(OrientationError):
                orientations.request_custom_orientation(guild, member, _future())

            assert OrientationSlot.objects.filter(guild=guild).count() == before


def describe_parse_proposed_time():
    def it_parses_a_24_hour_time():
        result = orientations.parse_proposed_time("2099-03-14", "17:30")
        local = timezone.localtime(result)
        assert (local.year, local.month, local.day, local.hour, local.minute) == (2099, 3, 14, 17, 30)

    def it_parses_a_12_hour_time_with_minutes():
        result = orientations.parse_proposed_time("2099-03-14", "5:30pm")
        assert timezone.localtime(result).hour == 17

    def it_parses_a_12_hour_time_on_the_hour():
        result = orientations.parse_proposed_time("2099-03-14", "9am")
        assert timezone.localtime(result).hour == 9

    def it_tolerates_spaces_and_casing():
        result = orientations.parse_proposed_time("2099-03-14", "5:30 PM")
        assert timezone.localtime(result).hour == 17

    def describe_with_an_unreadable_date():
        def it_raises_with_friendly_copy():
            with pytest.raises(OrientationError, match="couldn't read that time"):
                orientations.parse_proposed_time("March 14", "17:30")

    def describe_with_an_unreadable_time():
        def it_raises_with_friendly_copy():
            with pytest.raises(OrientationError, match="couldn't read that time"):
                orientations.parse_proposed_time("2099-03-14", "half past five")

    def describe_with_a_past_moment():
        def it_raises_a_future_error():
            with pytest.raises(OrientationError, match="already past"):
                orientations.parse_proposed_time("2000-01-01", "10:00")
