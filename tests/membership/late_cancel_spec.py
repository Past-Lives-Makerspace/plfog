"""BDD specs for the late cancellation policy resolver and its copy (#456, part 1).

One resolver, three owners: a guild orientation booking follows its guild's fee, an equipment
owned booking and an equipment reservation follow the equipment's fee, and the site switch
off zeroes every fee. Lateness is notice minus grace, strict, and only before the start.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import SiteConfiguration
from membership.late_cancel import (
    LateCancelPolicy,
    booking_sentence,
    cancel_sentence,
    policy_for,
    policy_for_equipment,
    policy_for_type,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    GuildOrientationSettingsFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _site(*, enabled: bool = True, notice: int = 24, grace: int = 2) -> SiteConfiguration:
    config = SiteConfiguration.load()
    config.late_cancel_fees_enabled = enabled
    config.late_cancel_notice_hours = notice
    config.late_cancel_grace_hours = grace
    config.save()
    return config


def _guild_booking(fee_cents: int) -> object:
    settings_obj = GuildOrientationSettingsFactory(late_cancel_fee_cents=fee_cents)
    return OrientationBookingFactory(slot=OrientationSlotFactory(guild=settings_obj.guild))


def describe_policy_for():
    def it_follows_the_guilds_fee_for_a_guild_orientation_booking():
        _site()
        policy = policy_for(_guild_booking(1500))
        assert policy == LateCancelPolicy(fee_cents=1500, notice_hours=24, grace_hours=2)
        assert policy.applies is True

    def it_follows_the_equipments_fee_for_an_equipment_owned_booking():
        _site()
        equipment = EquipmentFactory(late_cancel_fee_cents=2500)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        booking = OrientationBookingFactory(
            slot=OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        )
        assert policy_for(booking).fee_cents == 2500

    def it_follows_the_equipments_fee_for_a_reservation():
        _site()
        reservation = EquipmentReservationFactory(equipment=EquipmentFactory(late_cancel_fee_cents=1000))
        assert policy_for(reservation).fee_cents == 1000

    def it_reads_no_fee_for_a_guild_that_never_saved_settings():
        _site()
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(enabled_settings=False))
        policy = policy_for(booking)
        assert policy.fee_cents == 0
        assert policy.applies is False

    def it_reads_no_fee_for_a_guild_that_left_it_blank():
        _site()
        assert policy_for(_guild_booking(0)).applies is False

    def it_zeroes_every_fee_while_the_site_switch_is_off():
        _site(enabled=False)
        booking = _guild_booking(1500)
        reservation = EquipmentReservationFactory(equipment=EquipmentFactory(late_cancel_fee_cents=1000))
        assert policy_for(booking).fee_cents == 0
        assert policy_for(reservation).fee_cents == 0
        # The window still reads from Site Settings, so the copy is ready the moment a fee returns.
        assert policy_for(booking).notice_hours == 24

    def it_carries_the_sites_window():
        _site(notice=48, grace=4)
        policy = policy_for(_guild_booking(1500))
        assert (policy.notice_hours, policy.grace_hours) == (48, 4)


def describe_policy_for_type():
    def it_resolves_a_guild_owned_type_to_its_guild():
        _site()
        settings_obj = GuildOrientationSettingsFactory(late_cancel_fee_cents=1200)
        assert policy_for_type(OrientationTypeFactory(guild=settings_obj.guild)).fee_cents == 1200

    def it_resolves_an_equipment_owned_type_to_its_equipment():
        _site()
        equipment = EquipmentFactory(late_cancel_fee_cents=800)
        assert policy_for_type(OrientationTypeFactory(equipment_owned=True, equipment=equipment)).fee_cents == 800


def describe_policy_for_equipment():
    def it_reads_the_equipments_fee_in_the_sites_window():
        _site(notice=48, grace=4)
        policy = policy_for_equipment(EquipmentFactory(late_cancel_fee_cents=500))
        assert policy == LateCancelPolicy(fee_cents=500, notice_hours=48, grace_hours=4)

    def it_zeroes_the_fee_while_the_site_switch_is_off():
        _site(enabled=False)
        assert policy_for_equipment(EquipmentFactory(late_cancel_fee_cents=500)).fee_cents == 0


def describe_is_late():
    policy = LateCancelPolicy(fee_cents=1500, notice_hours=24, grace_hours=2)

    def it_is_not_late_exactly_at_notice_minus_grace():
        now = timezone.now()
        assert policy.is_late(now + timedelta(hours=22), now=now) is False

    def it_is_late_one_second_inside_the_line():
        now = timezone.now()
        assert policy.is_late(now + timedelta(hours=22) - timedelta(seconds=1), now=now) is True

    def it_is_not_late_well_ahead_of_the_line():
        now = timezone.now()
        assert policy.is_late(now + timedelta(days=3), now=now) is False

    def it_is_not_late_once_the_booking_has_started():
        now = timezone.now()
        assert policy.is_late(now, now=now) is False
        assert policy.is_late(now - timedelta(minutes=5), now=now) is False

    def it_defaults_now_to_this_moment():
        assert policy.is_late(timezone.now() + timedelta(hours=1)) is True
        assert policy.is_late(timezone.now() + timedelta(days=3)) is False

    def it_uses_the_policys_own_window():
        tight = LateCancelPolicy(fee_cents=1500, notice_hours=2, grace_hours=0)
        now = timezone.now()
        assert tight.is_late(now + timedelta(hours=2), now=now) is False
        assert tight.is_late(now + timedelta(hours=1, minutes=59), now=now) is True


def describe_fee_display():
    def it_shows_dollars_and_cents():
        assert LateCancelPolicy(fee_cents=1500, notice_hours=24, grace_hours=2).fee_display == "$15.00"
        assert LateCancelPolicy(fee_cents=1550, notice_hours=24, grace_hours=2).fee_display == "$15.50"
        assert LateCancelPolicy(fee_cents=5, notice_hours=24, grace_hours=2).fee_display == "$0.05"


def describe_booking_sentence():
    def it_names_the_notice_and_the_fee():
        policy = LateCancelPolicy(fee_cents=1500, notice_hours=24, grace_hours=2)
        assert booking_sentence(policy) == "Cancel at least 24 hours ahead. Cancelling later costs a $15.00 late fee."

    def it_reads_one_hour_in_the_singular():
        policy = LateCancelPolicy(fee_cents=1500, notice_hours=1, grace_hours=0)
        assert booking_sentence(policy) == "Cancel at least 1 hour ahead. Cancelling later costs a $15.00 late fee."

    def it_is_empty_with_no_fee():
        assert booking_sentence(LateCancelPolicy(fee_cents=0, notice_hours=24, grace_hours=2)) == ""


def describe_cancel_sentence():
    def it_names_the_window_and_the_fee():
        policy = LateCancelPolicy(fee_cents=1500, notice_hours=24, grace_hours=2)
        assert cancel_sentence(policy) == (
            "This is inside the 24 hour notice window, so a $15.00 late cancellation fee applies. "
            "You'll get a link to pay it."
        )

    def it_keeps_the_window_singular_as_an_adjective():
        policy = LateCancelPolicy(fee_cents=1500, notice_hours=1, grace_hours=0)
        assert cancel_sentence(policy).startswith("This is inside the 1 hour notice window, ")

    def it_is_empty_with_no_fee():
        assert cancel_sentence(LateCancelPolicy(fee_cents=0, notice_hours=24, grace_hours=2)) == ""

    def it_never_uses_a_dash_in_either_sentence():
        policy = LateCancelPolicy(fee_cents=1500, notice_hours=24, grace_hours=2)
        for sentence in (booking_sentence(policy), cancel_sentence(policy)):
            assert "-" not in sentence
            assert "—" not in sentence
            assert "–" not in sentence
