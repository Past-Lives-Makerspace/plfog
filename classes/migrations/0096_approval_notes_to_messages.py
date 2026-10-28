"""Carry every review decision's note into its class's message thread (#975).

Each non blank ``ClassApproval.notes`` becomes a reviewer's message on the class, written by the
approval's ``decided_by`` when it was recorded (otherwise authorless, which the thread signs as
the reviewer), dated when it was decided (or when the gate was opened, for a row with no
decision time). Oldest first follows from the dates. The notes stay on the approvals: they are
each decision's record, and the thread is the conversation around them.

Not carried: the system note an admin's final approval writes on the gates it closes for nobody
("Approved automatically when an admin gave final approval."), which posts nothing today either.
Only notes still in the database can move; a bounced row a resubmit already deleted is gone.

The reverse removes the messages the forward wrote: for each note, the message on its class with
the same text and date. Notes and every other message stay.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

AUTO_CLOSE_NOTE = "Approved automatically when an admin gave final approval."


def _noted(apps: Any) -> Any:
    ClassApproval = apps.get_model("classes", "ClassApproval")
    return ClassApproval.objects.exclude(notes__regex=r"^\s*$").exclude(notes=AUTO_CLOSE_NOTE).order_by("pk")


def notes_to_messages(apps: Any, schema_editor: Any) -> None:
    ClassMessage = apps.get_model("classes", "ClassMessage")
    ClassMessage.objects.bulk_create(
        ClassMessage(
            class_offering_id=approval.class_offering_id,
            author_id=approval.decided_by_id,
            body=approval.notes.strip(),
            created_at=approval.decided_at or approval.created_at,
        )
        for approval in _noted(apps).iterator()
    )


def messages_to_notes(apps: Any, schema_editor: Any) -> None:
    """Remove the carried messages; the notes never left their approvals."""
    ClassMessage = apps.get_model("classes", "ClassMessage")
    for approval in _noted(apps).iterator():
        ClassMessage.objects.filter(
            class_offering_id=approval.class_offering_id,
            body=approval.notes.strip(),
            created_at=approval.decided_at or approval.created_at,
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0095_class_messages"),
    ]

    operations = [
        migrations.RunPython(notes_to_messages, messages_to_notes),
    ]
