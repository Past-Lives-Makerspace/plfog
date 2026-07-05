from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0069_guild_is_featured"),
    ]

    operations = [
        migrations.AddField(
            model_name="guild",
            name="essential_rules",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Short essential/safety rules shown on your printable flyer. "
                    "Keep it brief — your full About is too long to print."
                ),
            ),
        ),
    ]
