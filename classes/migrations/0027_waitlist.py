from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("classes", "0026_class_approval"),
    ]

    operations = [
        migrations.AddField(
            model_name="registration",
            name="waitlist_notified_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "Stamped when this waitlisted registrant has been emailed that a "
                    "spot opened up. Used to avoid double-notifying and to expire the "
                    "claim window."
                ),
            ),
        ),
        migrations.AddField(
            model_name="classsettings",
            name="waitlist_claim_window_hours",
            field=models.PositiveIntegerField(
                default=24,
                help_text=(
                    "When a waitlisted person is notified that a spot opened, how many "
                    "hours they have to register before we move on to the next person."
                ),
            ),
        ),
    ]
