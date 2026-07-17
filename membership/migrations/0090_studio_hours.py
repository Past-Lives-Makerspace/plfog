# Generated for the Guild Studio Hours & weekly meetings feature.
#
# Reverse caveat (SEV5): the down-path re-adds the OLD, narrower
# ``ck_communityevent_guild_matches_type`` — which requires every non-``guild_meeting``
# row to have a NULL guild. Any ``studio_hours`` row (which carries a guild) violates that
# branch, so reversing this migration on a table that already holds studio-hours rows will
# be rejected by the database. Delete/convert the studio-hours rows before reversing.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0089_guild_discord_channel_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="communityevent",
            name="import_source_uid",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "The iCal UID of the Public-Calendar VEVENT this row was seeded from. Set only by "
                    "the one-time seed importer; used to re-run it without duplicating. Blank for "
                    "app-authored rows."
                ),
                max_length=500,
            ),
        ),
        migrations.AlterField(
            model_name="communityevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("guild_meeting", "Guild meeting / event"),
                    ("lead_meeting", "Guild Lead Meeting"),
                    ("community", "Community event"),
                    ("studio_hours", "Studio hours"),
                ],
                default="guild_meeting",
                help_text="What kind of event this is.",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="communityevent",
            name="recurrence",
            field=models.CharField(
                choices=[
                    ("none", "Does not repeat"),
                    ("weekly", "Every week"),
                    ("semi_monthly", "Twice a month"),
                    ("monthly", "Every month"),
                    ("every_2_months", "Every 2 months"),
                    ("every_3_months", "Every 3 months"),
                    ("every_6_months", "Every 6 months"),
                    ("yearly", "Every year"),
                ],
                default="none",
                help_text=(
                    "Whether and how often this event repeats. 'Every week' repeats on the same "
                    "weekday; the monthly options recur on the same weekday-of-month as the start "
                    "(e.g. the 2nd Saturday); 'Twice a month' adds the same weekday two weeks later."
                ),
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="communityevent",
            name="ck_communityevent_guild_matches_type",
        ),
        migrations.AddConstraint(
            model_name="communityevent",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("event_type__in", ["guild_meeting", "studio_hours"]), ("guild__isnull", False))
                    | models.Q(
                        models.Q(("event_type__in", ["guild_meeting", "studio_hours"]), _negated=True),
                        ("guild__isnull", True),
                    )
                ),
                name="ck_communityevent_guild_matches_type",
            ),
        ),
        migrations.AddConstraint(
            model_name="communityevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(("import_source_uid", ""), _negated=True),
                fields=("import_source_uid",),
                name="uq_communityevent_import_source_uid",
            ),
        ),
    ]
