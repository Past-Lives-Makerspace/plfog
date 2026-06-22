"""BDD specs for the orientation cron management commands."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from membership.models import OrientationSlot
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def describe_generate_orientation_slots_command():
    def it_creates_slots_and_reports():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationAvailabilityFactory(guild=guild)
        out = StringIO()
        call_command("generate_orientation_slots", stdout=out)
        assert "Created" in out.getvalue()
        assert OrientationSlot.objects.filter(guild=guild).exists()


def describe_auto_complete_orientations_command():
    def it_completes_past_confirmed_bookings():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        slot = OrientationSlotFactory(
            guild=guild,
            starts_at=timezone.now() - timedelta(hours=3),
            ends_at=timezone.now() - timedelta(hours=2),
        )
        booking = OrientationBookingFactory(slot=slot)
        booking.confirm()
        out = StringIO()
        call_command("auto_complete_orientations", stdout=out)
        assert "Completed" in out.getvalue()
        booking.refresh_from_db()
        assert booking.is_completed is True
