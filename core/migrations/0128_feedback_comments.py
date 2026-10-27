"""Feedback request comments: the thread, its photos, and who is waiting on whom.

Adds before it removes: feedback_notes_to_comments copies each note into the thread, and staff_note (retired here, its
help text says so) stays until part 2 drops it, so the code serving during the deploy keeps working.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0127_retire_tour_booking_service"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="feedbackrequest",
            name="staff_note",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Retired. Notes now live in the comment thread; part 2 drops this column.",
            ),
        ),
        migrations.AddField(
            model_name="feedbackrequest",
            name="member_replied_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the sender last wrote a comment on it.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="feedbackrequest",
            name="staff_seen_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When an admin last opened its page in the Feedback inbox.",
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="FeedbackComment",
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
                        help_text="Who wrote it: the sender or an admin. Blank for a note carried over from before comments.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="feedback_comments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "request",
                    models.ForeignKey(
                        help_text="The request this comment is on.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="comments",
                        to="core.feedbackrequest",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "pk"],
            },
        ),
        migrations.CreateModel(
            name="FeedbackCommentPhoto",
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
                    "image",
                    models.ImageField(
                        help_text="The uploaded photo, on the default storage.",
                        upload_to="feedback/%Y/%m/",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, help_text="When it was uploaded."),
                ),
                (
                    "comment",
                    models.ForeignKey(
                        help_text="The comment this photo was posted with.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photos",
                        to="core.feedbackcomment",
                    ),
                ),
            ],
            options={
                "ordering": ["pk"],
            },
        ),
    ]
