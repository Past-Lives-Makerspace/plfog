"""Retire ``FeedbackRequest.staff_note`` from the model, and add the Not a bug and Closed statuses.

First, any note still on a request that the thread does not already carry (old code could write one
during part 1's deploy window) becomes a comment with no author, as feedback_notes_to_comments did. A
note that migration already copied is still in the column, so a request whose thread has an
authorless comment with the same words is skipped rather than given a second copy.

Add before you remove (STANDARDS.md section 10), as 0124 did: the part 1 release still reads and
writes the column while this migrates, so the column stays. It gets a database default, so the new
code's inserts, which no longer name it, keep working, and only the model forgets it. A later
release drops the column. The reverse puts the field back in the model, removes the database default
and fills each note from the request's oldest authorless comment, leaving every comment in place.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations, models
from django.utils import timezone


def copy_leftover_notes(apps: Any, schema_editor: Any) -> None:
    FeedbackRequest = apps.get_model("core", "FeedbackRequest")
    FeedbackComment = apps.get_model("core", "FeedbackComment")
    for request in FeedbackRequest.objects.exclude(staff_note=""):
        note = request.staff_note.strip()
        if note and not FeedbackComment.objects.filter(request=request, author=None, body=note).exists():
            FeedbackComment.objects.create(request=request, author=None, body=note, created_at=timezone.now())


def restore_notes(apps: Any, schema_editor: Any) -> None:
    FeedbackComment = apps.get_model("core", "FeedbackComment")
    seen: set[int] = set()
    for comment in FeedbackComment.objects.filter(author__isnull=True).order_by("request_id", "created_at", "pk"):
        if comment.request_id in seen:
            continue
        seen.add(comment.request_id)
        comment.request.staff_note = comment.body
        comment.request.save(update_fields=["staff_note"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0129_feedback_notes_to_comments"),
    ]

    operations = [
        migrations.RunPython(copy_leftover_notes, restore_notes),
        migrations.AlterField(
            model_name="feedbackrequest",
            name="staff_note",
            field=models.TextField(
                blank=True,
                db_default="",
                default="",
                help_text="Retired. Notes now live in the comment thread; a later release drops this column.",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="feedbackrequest",
                    name="staff_note",
                ),
            ],
        ),
        migrations.AlterField(
            model_name="feedbackrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("received", "Received"),
                    ("planned", "Planned"),
                    ("building", "Building"),
                    ("live", "Live"),
                    ("not_planned", "Not planned"),
                    ("not_a_bug", "Not a bug"),
                    ("closed", "Closed"),
                ],
                default="received",
                help_text="Where the request stands. The sender sees it on the Feedback page.",
                max_length=12,
            ),
        ),
    ]
