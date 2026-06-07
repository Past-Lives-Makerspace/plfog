"""Add instructor profile fields to Member (slug, website, social handle)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0031_remove_calendarevent_uq_calendarevent_guild_uid_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="member",
            name="instructor_slug",
            field=models.SlugField(
                blank=True,
                max_length=255,
                help_text="URL slug for this member's public instructor profile. Non-empty = teaches classes.",
            ),
        ),
        migrations.AddField(
            model_name="member",
            name="instructor_website",
            field=models.URLField(blank=True, help_text="Instructor personal site."),
        ),
        migrations.AddField(
            model_name="member",
            name="instructor_social_handle",
            field=models.CharField(
                blank=True,
                max_length=255,
                help_text="e.g. @handle on primary social (instructor profile).",
            ),
        ),
    ]
