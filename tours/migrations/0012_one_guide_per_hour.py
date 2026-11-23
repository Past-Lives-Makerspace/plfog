"""Every guide sets their own minimum notice (#1312).

Adds each guide's own minimum notice in minutes (``TourGuideSettings.min_notice_minutes``, blank
meaning Tour Settings' default, never a floor). Every guide starts on the default: the old hours
notice is not copied (Felix: the 25 hour values only existed because the old field refused 24).
The hours column stays one release, unread, so the release still serving during the deploy keeps
working (STANDARDS 10).

No step moves guides off Confirm First (#1309): production was checked on 2026-11-23 with every
guide already on Confirm Automatically (Felix moved them by hand) and no request waiting, and
nobody can pick Confirm First any more, so there was nothing to change or book.
"""

import django.core.validators
from django.db import migrations, models


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
