from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0138_discord_leadership_role_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfiguration",
            name="discord_leadership_directory_channel_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="The channel id of #leadership-directory, where FOG keeps a post that mirrors the Leadership Directory (#1149). Blank turns the post off.",
                max_length=30,
                verbose_name="Leadership directory channel id",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="discord_leadership_directory_hash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="SHA256 of the directory post as last sent; an equal render sends nothing. Written by the sync.",
                max_length=64,
                verbose_name="Leadership directory content hash",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="discord_leadership_directory_message_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="The ids of FOG's messages in the leadership directory channel, in order. Written by the sync.",
                verbose_name="Leadership directory message ids",
            ),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="discord_leadership_directory_updated_on",
            field=models.DateField(
                blank=True,
                help_text="The Pacific date the directory post's content last changed; its footer shows it. Written by the sync.",
                null=True,
                verbose_name="Leadership directory updated on",
            ),
        ),
    ]
