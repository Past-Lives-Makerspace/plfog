"""Carry each feedback request's staff note into its thread as the first staff comment.

Nothing records which admin wrote a note, so the comment has no author and the thread signs it
"Past Lives". It is dated when the request's status last changed, the closest record of when the
note was written. The reverse puts each carried over comment back as the note and deletes it.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations


def notes_to_comments(apps: Any, schema_editor: Any) -> None:
    FeedbackRequest = apps.get_model("core", "FeedbackRequest")
    FeedbackComment = apps.get_model("core", "FeedbackComment")
    for request in FeedbackRequest.objects.exclude(staff_note=""):
        note = request.staff_note.strip()
        if note:
            FeedbackComment.objects.create(
                request=request, author=None, body=note, created_at=request.status_changed_at
            )


def comments_to_notes(apps: Any, schema_editor: Any) -> None:
    FeedbackComment = apps.get_model("core", "FeedbackComment")
    for comment in FeedbackComment.objects.filter(author__isnull=True).select_related("request"):
        comment.request.staff_note = comment.body
        comment.request.save(update_fields=["staff_note"])
        comment.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0127_feedback_comments"),
    ]

    operations = [
        migrations.RunPython(notes_to_comments, comments_to_notes),
    ]
