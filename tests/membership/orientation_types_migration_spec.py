"""Data-migration spec for 0157 — backfilling one OrientationType per guild.

Uses Django's ``MigrationExecutor`` (same approach as the 0154 spec) so fixtures are
built against the 0156 state — the type table and nullable FKs exist, and the
per-orientation config still lives on GuildOrientationSettings. Each test restores
the schema to head in a ``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

_APP = "membership"
_BEFORE = "0156_orientationtype_and_nullable_fks"
_AFTER = "0157_backfill_orientation_types"
_HEAD = "0158_orientation_type_required"


def _migrate(target: str):
    """Migrate the membership app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_member(apps, name: str):
    MembershipPlan = apps.get_model(_APP, "MembershipPlan")
    plan, _ = MembershipPlan.objects.get_or_create(name="Std", defaults={"monthly_price": Decimal("10")})
    Member = apps.get_model(_APP, "Member")
    return Member.objects.create(membership_plan=plan, full_legal_name=name)


def _orientation_rows(apps, guild, member):
    """One rule, one slot on the rule, one booking on the slot — the full chain."""
    OrientationAvailability = apps.get_model(_APP, "OrientationAvailability")
    OrientationSlot = apps.get_model(_APP, "OrientationSlot")
    OrientationBooking = apps.get_model(_APP, "OrientationBooking")
    rule = OrientationAvailability.objects.create(guild=guild, weekday=1, start_time="18:00", end_time="19:00", seats=4)
    starts = timezone.now() + timedelta(days=2)
    slot = OrientationSlot.objects.create(
        guild=guild, availability=rule, starts_at=starts, ends_at=starts + timedelta(hours=1), seats=4
    )
    booking = OrientationBooking.objects.create(slot=slot, guild=guild, member=member)
    return rule, slot, booking


@pytest.mark.django_db(transaction=True)
def describe_migration_0157_backfill_orientation_types():
    def it_creates_one_type_per_settings_guild_and_backfills_every_row():
        try:
            apps = _migrate(_BEFORE)
            Guild = apps.get_model(_APP, "Guild")
            GuildOrientationSettings = apps.get_model(_APP, "GuildOrientationSettings")
            guild = Guild.objects.create(name="Wood Guild", slug="wood-guild")
            GuildOrientationSettings.objects.create(
                guild=guild,
                is_enabled=True,
                default_duration_minutes=45,
                price_cents=1200,
                default_seats=3,
                default_location="Front Desk",
            )
            member = _make_member(apps, "Backfill B")
            rule, slot, booking = _orientation_rows(apps, guild, member)

            apps = _migrate(_AFTER)
            OrientationType = apps.get_model(_APP, "OrientationType")
            orientation_type = OrientationType.objects.get(guild_id=guild.pk)
            assert orientation_type.name == "Orientation"
            assert orientation_type.duration_minutes == 45
            assert orientation_type.price_cents == 1200
            assert orientation_type.default_seats == 3
            assert orientation_type.default_location == "Front Desk"
            assert orientation_type.is_active is True
            OrientationAvailability = apps.get_model(_APP, "OrientationAvailability")
            OrientationSlot = apps.get_model(_APP, "OrientationSlot")
            OrientationBooking = apps.get_model(_APP, "OrientationBooking")
            assert OrientationAvailability.objects.get(pk=rule.pk).orientation_type_id == orientation_type.pk
            assert OrientationSlot.objects.get(pk=slot.pk).orientation_type_id == orientation_type.pk
            assert OrientationBooking.objects.get(pk=booking.pk).orientation_type_id == orientation_type.pk
        finally:
            _migrate(_HEAD)

    def it_covers_a_guild_with_orientation_rows_but_no_settings():
        # Historically possible (the factory's enabled_settings=False path) — the
        # backfill must still leave zero NULL FKs for 0158's non-null alter.
        try:
            apps = _migrate(_BEFORE)
            Guild = apps.get_model(_APP, "Guild")
            guild = Guild.objects.create(name="Orphan Guild", slug="orphan-guild")
            member = _make_member(apps, "Orphan O")
            rule, slot, booking = _orientation_rows(apps, guild, member)

            apps = _migrate(_AFTER)
            OrientationType = apps.get_model(_APP, "OrientationType")
            orientation_type = OrientationType.objects.get(guild_id=guild.pk)
            assert orientation_type.name == "Orientation"
            assert orientation_type.duration_minutes == 60  # model defaults, no settings to copy
            assert orientation_type.price_cents == 0
            OrientationSlot = apps.get_model(_APP, "OrientationSlot")
            assert OrientationSlot.objects.get(pk=slot.pk).orientation_type_id == orientation_type.pk
        finally:
            _migrate(_HEAD)

    def it_reverses_by_restoring_settings_config_and_deleting_the_types():
        try:
            apps = _migrate(_AFTER)
            Guild = apps.get_model(_APP, "Guild")
            GuildOrientationSettings = apps.get_model(_APP, "GuildOrientationSettings")
            OrientationType = apps.get_model(_APP, "OrientationType")
            guild = Guild.objects.create(name="Reverse Guild", slug="reverse-guild")
            GuildOrientationSettings.objects.create(guild=guild, is_enabled=True)
            orientation_type = OrientationType.objects.create(
                guild=guild,
                name="Orientation",
                duration_minutes=75,
                price_cents=2500,
                default_seats=6,
                default_location="The Annex",
            )
            member = _make_member(apps, "Reverse R")
            OrientationAvailability = apps.get_model(_APP, "OrientationAvailability")
            OrientationSlot = apps.get_model(_APP, "OrientationSlot")
            OrientationBooking = apps.get_model(_APP, "OrientationBooking")
            rule = OrientationAvailability.objects.create(
                guild=guild,
                orientation_type=orientation_type,
                weekday=2,
                start_time="10:00",
                end_time="11:00",
                seats=4,
            )
            starts = timezone.now() + timedelta(days=3)
            slot = OrientationSlot.objects.create(
                guild=guild,
                orientation_type=orientation_type,
                availability=rule,
                starts_at=starts,
                ends_at=starts + timedelta(hours=1),
                seats=4,
            )
            booking = OrientationBooking.objects.create(
                slot=slot, guild=guild, orientation_type=orientation_type, member=member
            )

            apps = _migrate(_BEFORE)
            OrientationType = apps.get_model(_APP, "OrientationType")
            GuildOrientationSettings = apps.get_model(_APP, "GuildOrientationSettings")
            assert OrientationType.objects.count() == 0
            restored = GuildOrientationSettings.objects.get(guild_id=guild.pk)
            assert restored.default_duration_minutes == 75
            assert restored.price_cents == 2500
            assert restored.default_seats == 6
            assert restored.default_location == "The Annex"
            OrientationAvailability = apps.get_model(_APP, "OrientationAvailability")
            OrientationSlot = apps.get_model(_APP, "OrientationSlot")
            OrientationBooking = apps.get_model(_APP, "OrientationBooking")
            assert OrientationAvailability.objects.get(pk=rule.pk).orientation_type_id is None
            assert OrientationSlot.objects.get(pk=slot.pk).orientation_type_id is None
            assert OrientationBooking.objects.get(pk=booking.pk).orientation_type_id is None
        finally:
            _migrate(_HEAD)
