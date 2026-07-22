from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0098_discordlinknudge"),
    ]

    operations = [
        migrations.AddField(
            model_name="guild",
            name="is_public",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "On: anyone with the link can read this guild's page on the public guilds site. "
                    "Off: the page stays inside the member hub, and visitors get a short, friendly note instead."
                ),
                verbose_name="Share this guild's page publicly",
            ),
        ),
    ]
