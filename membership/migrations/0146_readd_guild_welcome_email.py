# Re-adds the four welcome-email columns to GuildOrientationSettings, restoring the
# per-guild join welcome email that migration 0145 dropped when the "Join This Guild"
# button was temporarily removed. This is a pure AddField x4, so Django auto-generates
# the reverse (RemoveField x4) and it runs backward cleanly with no hand-written reverse.
#
# NOTE: this restores EMPTY columns. Any welcome-email subject/body a guild had stored
# before the 0145 drop is NOT recoverable (it was destroyed then). Leads re-author from
# the seeded default copy (membership/guild_welcome_copy.py).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0145_remove_guild_welcome_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="guildorientationsettings",
            name="welcome_email_body",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Body of the welcome email (your personal note; line breaks preserved).",
            ),
        ),
        migrations.AddField(
            model_name="guildorientationsettings",
            name="welcome_email_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Send a welcome email when a member joins this guild. On by default; "
                    "leave the subject and body blank to send the standard welcome, or write your own."
                ),
            ),
        ),
        migrations.AddField(
            model_name="guildorientationsettings",
            name="welcome_email_subject",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Subject line of the welcome email.",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="guildorientationsettings",
            name="welcome_email_updated_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the welcome email was last edited.",
                null=True,
            ),
        ),
    ]
