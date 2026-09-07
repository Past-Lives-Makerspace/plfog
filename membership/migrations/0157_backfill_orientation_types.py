"""Backfill one "Orientation" type per guild and point every orientation row at it.

Forward: each guild with a GuildOrientationSettings row gets exactly one
OrientationType named "Orientation" carrying the settings row's duration, price,
seats, and location; every existing availability rule, slot, and booking is
pointed at its guild's type. Guilds that have orientation rows but no settings
row (possible historically) get a defaults-valued type so 0158 can make the FKs
non-nullable.

Reverse: a REAL reverse — the per-orientation config is copied back onto each
guild's settings row from its first type (by sort order, the backfilled
"Orientation" row in a pure roundtrip), every FK is nulled, and all
OrientationType rows are deleted.
"""

from django.db import migrations

_TYPE_NAME = "Orientation"


def _forward(apps, schema_editor):
    GuildOrientationSettings = apps.get_model("membership", "GuildOrientationSettings")
    OrientationType = apps.get_model("membership", "OrientationType")
    OrientationAvailability = apps.get_model("membership", "OrientationAvailability")
    OrientationSlot = apps.get_model("membership", "OrientationSlot")
    OrientationBooking = apps.get_model("membership", "OrientationBooking")

    type_by_guild: dict[int, int] = {}
    for settings_obj in GuildOrientationSettings.objects.all():
        orientation_type = OrientationType.objects.create(
            guild_id=settings_obj.guild_id,
            name=_TYPE_NAME,
            duration_minutes=settings_obj.default_duration_minutes,
            price_cents=settings_obj.price_cents,
            default_seats=settings_obj.default_seats,
            default_location=settings_obj.default_location,
            sort_order=0,
        )
        type_by_guild[settings_obj.guild_id] = orientation_type.pk

    def type_for(guild_id: int) -> int:
        if guild_id not in type_by_guild:
            # A guild with orientation rows but no settings row: model-default type,
            # so 0158's non-null alter never hits an orphan.
            orientation_type = OrientationType.objects.create(guild_id=guild_id, name=_TYPE_NAME, sort_order=0)
            type_by_guild[guild_id] = orientation_type.pk
        return type_by_guild[guild_id]

    for model in (OrientationAvailability, OrientationSlot, OrientationBooking):
        for guild_id in model.objects.values_list("guild_id", flat=True).distinct():
            model.objects.filter(guild_id=guild_id).update(orientation_type_id=type_for(guild_id))


def _reverse(apps, schema_editor):
    GuildOrientationSettings = apps.get_model("membership", "GuildOrientationSettings")
    OrientationType = apps.get_model("membership", "OrientationType")
    OrientationAvailability = apps.get_model("membership", "OrientationAvailability")
    OrientationSlot = apps.get_model("membership", "OrientationSlot")
    OrientationBooking = apps.get_model("membership", "OrientationBooking")

    for settings_obj in GuildOrientationSettings.objects.all():
        first_type = (
            OrientationType.objects.filter(guild_id=settings_obj.guild_id).order_by("sort_order", "name").first()
        )
        if first_type is None:
            continue
        settings_obj.default_duration_minutes = first_type.duration_minutes
        settings_obj.price_cents = first_type.price_cents
        settings_obj.default_seats = first_type.default_seats
        settings_obj.default_location = first_type.default_location
        settings_obj.save(
            update_fields=["default_duration_minutes", "price_cents", "default_seats", "default_location"]
        )
    for model in (OrientationAvailability, OrientationSlot, OrientationBooking):
        model.objects.update(orientation_type=None)
    OrientationType.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0156_orientationtype_and_nullable_fks"),
    ]

    operations = [
        migrations.RunPython(_forward, _reverse),
    ]
