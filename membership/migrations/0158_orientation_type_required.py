"""Make orientation_type required, widen the active-booking constraint to per-type,
and drop the per-orientation config fields that moved onto OrientationType.

Runs after 0157's backfill, so every rule/slot/booking already points at a type.
The active-booking uniqueness moves from (guild, member) to (orientation_type,
member): a member may now hold one live booking per orientation type. The
GuildOrientationSettings duration/price/seats/location fields are removed — each
OrientationType carries its own (reversing re-adds them with defaults; 0157's
reverse then restores their values from the backfilled type).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0157_backfill_orientation_types"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orientationavailability",
            name="orientation_type",
            field=models.ForeignKey(
                help_text="The orientation type slots generated from this rule are for.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rules",
                to="membership.orientationtype",
            ),
        ),
        migrations.AlterField(
            model_name="orientationslot",
            name="orientation_type",
            field=models.ForeignKey(
                help_text="The orientation type this slot is for.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="slots",
                to="membership.orientationtype",
            ),
        ),
        migrations.AlterField(
            model_name="orientationbooking",
            name="orientation_type",
            field=models.ForeignKey(
                help_text=(
                    "Denormalized from the slot (like guild) for cheap filtering and the per-type duplicate guard."
                ),
                on_delete=django.db.models.deletion.CASCADE,
                related_name="bookings",
                to="membership.orientationtype",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="orientationbooking",
            name="uq_orientationbooking_active_per_guild",
        ),
        migrations.AddConstraint(
            model_name="orientationbooking",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["pending_payment", "requested", "confirmed"])),
                fields=("orientation_type", "member"),
                name="uq_orientationbooking_active_per_type",
            ),
        ),
        migrations.RemoveField(
            model_name="guildorientationsettings",
            name="default_seats",
        ),
        migrations.RemoveField(
            model_name="guildorientationsettings",
            name="default_location",
        ),
        migrations.RemoveField(
            model_name="guildorientationsettings",
            name="default_duration_minutes",
        ),
        migrations.RemoveField(
            model_name="guildorientationsettings",
            name="price_cents",
        ),
    ]
