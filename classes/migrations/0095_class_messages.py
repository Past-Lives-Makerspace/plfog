"""The message thread on a class, between its instructors and its reviewers (core.threads, #975)."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0094_guild_class_types"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClassMessage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("body", models.TextField(help_text="What they wrote.")),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now,
                        help_text="When it was posted.",
                    ),
                ),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        help_text="Who wrote it. Blank for a note carried over from before the thread existed.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "class_offering",
                    models.ForeignKey(
                        help_text="The class this message is on.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="classes.classoffering",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "pk"],
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="ClassMessagePhoto",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, help_text="When it was uploaded."),
                ),
                (
                    "image",
                    models.ImageField(
                        help_text="The uploaded photo, on the default storage.",
                        upload_to="class-messages/%Y/%m/",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        help_text="The message this photo was posted with.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photos",
                        to="classes.classmessage",
                    ),
                ),
            ],
            options={
                "ordering": ["pk"],
                "abstract": False,
            },
        ),
    ]
