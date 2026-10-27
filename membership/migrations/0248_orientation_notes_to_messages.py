"""Carry each orientation booking's notes into its message thread, then retire the two fields.

``member_note`` becomes the member's first message, written by the member's account and dated when
the booking was requested. ``lead_note`` becomes a message with no author, which the thread signs as
the guild (or the equipment), dated when the booking was declined (the one road that wrote it), or
when it was requested if it never was. Blank notes add nothing.

Add before you remove (STANDARDS.md section 10), as core 0130 did: the release still serving while
this migrates reads and writes both columns, so they stay. Each gets a database default, so the new
code's inserts, which no longer name them, keep working, and only the model forgets them. A later
release drops the columns; it should first copy any note the old release wrote during the deploy.
The reverse puts both fields back in the model, removes the defaults and fills each note from the
thread (the member's oldest message, the newest staff message), leaving every message in place.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations, models


def notes_to_messages(apps: Any, schema_editor: Any) -> None:
    OrientationBooking = apps.get_model("membership", "OrientationBooking")
    Message = apps.get_model("membership", "OrientationBookingMessage")
    carried = []
    noted = OrientationBooking.objects.exclude(member_note="", lead_note="").select_related("member")
    for booking in noted.iterator():
        member_note = booking.member_note.strip()
        if member_note:
            carried.append(
                Message(
                    booking=booking,
                    author_id=booking.member.user_id,
                    body=member_note,
                    created_at=booking.requested_at,
                )
            )
        lead_note = booking.lead_note.strip()
        if lead_note:
            carried.append(
                Message(
                    booking=booking,
                    author=None,
                    body=lead_note,
                    created_at=booking.declined_at or booking.requested_at,
                )
            )
    Message.objects.bulk_create(carried)


def messages_to_notes(apps: Any, schema_editor: Any) -> None:
    """Fill the notes back from the thread: the member's oldest message, the newest staff message."""
    OrientationBooking = apps.get_model("membership", "OrientationBooking")
    Message = apps.get_model("membership", "OrientationBookingMessage")
    booking_ids = Message.objects.values_list("booking_id", flat=True).distinct()
    for booking in OrientationBooking.objects.filter(pk__in=booking_ids).select_related("member"):
        member_user_id = booking.member.user_id
        messages = list(Message.objects.filter(booking=booking).order_by("created_at", "pk"))
        from_member = [m for m in messages if m.author_id is not None and m.author_id == member_user_id]
        from_staff = [m for m in messages if m.author_id is None or m.author_id != member_user_id]
        booking.member_note = from_member[0].body if from_member else ""
        booking.lead_note = from_staff[-1].body if from_staff else ""
        booking.save(update_fields=["member_note", "lead_note"])


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0247_orientation_booking_messages"),
    ]

    operations = [
        migrations.RunPython(notes_to_messages, messages_to_notes),
        migrations.AlterField(
            model_name="orientationbooking",
            name="member_note",
            field=models.TextField(
                blank=True,
                db_default="",
                default="",
                help_text="Retired. The member's note is now the first message on the thread; a later release drops this column.",
            ),
        ),
        migrations.AlterField(
            model_name="orientationbooking",
            name="lead_note",
            field=models.TextField(
                blank=True,
                db_default="",
                default="",
                help_text="Retired. Notes to the member are now messages on the thread; a later release drops this column.",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="orientationbooking", name="member_note"),
                migrations.RemoveField(model_name="orientationbooking", name="lead_note"),
            ],
        ),
    ]
