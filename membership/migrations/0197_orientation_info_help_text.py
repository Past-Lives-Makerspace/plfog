# Orientation info is rich text (#502): help text only, no DDL.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0196_remove_orientation_external_signup_url"),
    ]

    operations = [
        migrations.AlterField(
            model_name="guildorientationsettings",
            name="info",
            field=models.TextField(
                blank=True, default="", help_text="Orientation info shown to members before they book."
            ),
        ),
    ]
