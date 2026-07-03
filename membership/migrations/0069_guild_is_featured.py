from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0068_orginfopage_orglink_orgfaqitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="guild",
            name="is_featured",
            field=models.BooleanField(
                default=False,
                help_text="Pin this guild to the top of the public guilds directory.",
            ),
        ),
    ]
