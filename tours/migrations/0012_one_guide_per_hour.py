"""Every guide sets their own minimum notice (#1312).

Adds each guide's own minimum notice in minutes (``TourGuideSettings.min_notice_minutes``, blank
meaning Tour Settings' default, never a floor) and copies every saved notice into it, hours to
minutes; the reverse copies each back into hours, rounded up. The hours column stays one release,
unread, so the release still serving during the deploy keeps working (STANDARDS 10).

No step moves guides off Confirm First (#1309): production was checked on 2026-11-23 with every
guide already on Confirm Automatically (Felix moved them by hand) and no request waiting, and
nobody can pick Confirm First any more, so there was nothing to change or book.
"""

from typing import Any

import django.core.validators
from django.db import migrations, models


def notice_into_minutes(apps: Any, schema_editor: Any) -> None:
    """Every saved notice, hours to minutes (#1312)."""
    guide_settings = apps.get_model("tours", "TourGuideSettings")
    guide_settings.objects.update(min_notice_minutes=models.F("min_notice_hours") * 60)


def notice_into_hours(apps: Any, schema_editor: Any) -> None:
    """Each notice a guide set, back into hours, rounded up and held to 0 to 336."""
    guide_settings = apps.get_model("tours", "TourGuideSettings")
    for row in guide_settings.objects.filter(min_notice_minutes__isnull=False):
        row.min_notice_hours = min(-(-row.min_notice_minutes // 60), 336)
        row.save(update_fields=["min_notice_hours"])


class Migration(migrations.Migration):
    dependencies = [
        ("tours", "0011_tourbooking_guest_answers"),
    ]

    operations = [
        migrations.AddField(
            model_name="tourguidesettings",
            name="min_notice_minutes",
            field=models.PositiveIntegerField(
                blank=True,
                default=None,
                help_text=(
                    "How many minutes before a tour visitors on the website can still book this guide (#1312; 0 is "
                    "no notice). Blank: Tour Settings' default minimum notice. The kiosk and staff ignore it."
                ),
                null=True,
                validators=[django.core.validators.MaxValueValidator(20160, message="At most two weeks.")],
            ),
        ),
        migrations.RunPython(notice_into_minutes, notice_into_hours),
        migrations.AlterField(
            model_name="facilitytoursettings",
            name="min_notice_hours",
            field=models.PositiveSmallIntegerField(
                default=24,
                help_text=(
                    "The default minimum notice (#1312): how many hours before a tour visitors on the website can "
                    "still book it, for a guide who has not set their own on My Hours. Never a floor: a guide may "
                    "pick less or more."
                ),
                validators=[django.core.validators.MaxValueValidator(336, "Use 0 to 336 hours.")],
            ),
        ),
    ]
