# Orientation signups stay in the app (#502): the outside signup link goes away on both
# the guild settings row and the orientation type. Real DDL: two columns are dropped.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0195_announcementdraft_added_recipients"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="guildorientationsettings",
            name="external_signup_url",
        ),
        migrations.RemoveField(
            model_name="orientationtype",
            name="external_signup_url",
        ),
    ]
