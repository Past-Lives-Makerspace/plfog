from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0091_communityevent_discord_sync"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="guild",
            name="is_public",
        ),
    ]
