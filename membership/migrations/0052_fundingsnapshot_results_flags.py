from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0051_votingsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="fundingsnapshot",
            name="is_auto",
            field=models.BooleanField(
                default=False,
                help_text="True when this snapshot was taken automatically at cycle end (vs. by an admin).",
            ),
        ),
        migrations.AddField(
            model_name="fundingsnapshot",
            name="results_send_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="How many times the results email has been sent (>=1 after the first send; supports resend).",
            ),
        ),
        migrations.AddField(
            model_name="fundingsnapshot",
            name="results_sent_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the member results email was sent for this snapshot. Null = pending the admin's review & send.",
                null=True,
            ),
        ),
    ]
