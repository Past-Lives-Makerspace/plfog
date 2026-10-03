# Orientation signups stay in the app (#502): the outside signup link goes away on both
# the guild settings row and the orientation type. Add before you remove (STANDARDS.md
# section 10): the columns turn nullable here and leave Django's state, so the release
# still serving during the deploy keeps reading them and the new one inserts without
# them. The real DROP COLUMN ships in the next migration after this release is live.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0195_announcementdraft_added_recipients"),
    ]

    operations = [
        migrations.AlterField(
            model_name="guildorientationsettings",
            name="external_signup_url",
            field=models.URLField(blank=True, default="", max_length=500, null=True),
        ),
        migrations.AlterField(
            model_name="orientationtype",
            name="external_signup_url",
            field=models.URLField(blank=True, default="", max_length=500, null=True),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="guildorientationsettings", name="external_signup_url"),
                migrations.RemoveField(model_name="orientationtype", name="external_signup_url"),
            ],
        ),
    ]
