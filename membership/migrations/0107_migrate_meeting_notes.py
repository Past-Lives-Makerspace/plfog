"""Fold the legacy ``GuildMeetingNote`` rows into the new ``Meeting`` model.

Each note becomes a past **approved** meeting in the same guild: the Markdown body is
rendered to sanitized HTML into ``other_notes``, the note's author and post date become
the approval stamps, and every attachment row is copied to ``MeetingAttachment``
**referencing the same stored file name** — no file I/O, no copy, no move. Titles
containing "monthly" (case-insensitive) map to the regular Monthly meeting; every other
title becomes a named Special meeting carrying the old headline, so nothing is lost.

Idempotent: each created ``Meeting`` records its source pk in ``legacy_note_id``
(unique when set), and notes whose pk is already recorded are skipped on re-run.

The source ``GuildMeetingNote`` rows are left completely untouched — they survive until
the separate retirement migration in a later release.

Reverse contract: a **genuine restore**. It deletes only the ``Meeting`` rows marked
with ``legacy_note_id`` (cascading their attachment rows — row deletion never touches
storage files, which the original notes still own). Hand-created meetings and the
untouched source notes are left exactly as they were, so unapplying this migration
returns the database to its pre-migration state.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

#: ``Meeting.special_title`` is capped at 120 chars while note titles allowed 300 —
#: over-long special titles are truncated so the forward pass cannot fail on Postgres.
_SPECIAL_TITLE_MAX = 120


def _looks_monthly(title: str) -> bool:
    """The spec's dumb heuristic: case-insensitive "monthly" anywhere in the title."""
    return "monthly" in title.lower()


def migrate_meeting_notes(apps: Any, schema_editor: Any) -> None:
    # A plain function import, not a model — safe to use against historical models.
    from membership.markdown import render_markdown

    GuildMeetingNote = apps.get_model("membership", "GuildMeetingNote")
    Meeting = apps.get_model("membership", "Meeting")
    MeetingAttachment = apps.get_model("membership", "MeetingAttachment")

    already_migrated = set(
        Meeting.objects.filter(legacy_note_id__isnull=False).values_list("legacy_note_id", flat=True)
    )
    notes = GuildMeetingNote.objects.exclude(pk__in=already_migrated).prefetch_related("attachments").order_by("pk")
    for note in notes:
        monthly = _looks_monthly(note.title)
        # Historical models have no model methods — set every field directly. The
        # special-title check constraint holds: special_title is only non-blank when
        # is_special is True.
        meeting = Meeting.objects.create(
            guild_id=note.guild_id,
            status="approved",
            scheduled_date=note.meeting_date,
            is_special=not monthly,
            special_title="" if monthly else note.title[:_SPECIAL_TITLE_MAX],
            special_notes="",
            other_notes=render_markdown(note.body) if note.body else "",
            approved_at=note.created_at,
            approved_by_id=note.created_by_id,
            created_by_id=note.created_by_id,
            legacy_note_id=note.pk,
        )
        for attachment in note.attachments.all():
            MeetingAttachment.objects.create(
                meeting=meeting,
                label=attachment.label,
                file=attachment.file.name,  # the same stored name — no file I/O
                url=attachment.url,
                sort_order=attachment.sort_order,
            )


def unmigrate_meeting_notes(apps: Any, schema_editor: Any) -> None:
    """Delete only what the forward pass created: ``Meeting`` rows marked with a
    ``legacy_note_id``. Attachment rows cascade (rows only — storage files are never
    deleted; the original notes still own them). Hand-created meetings and the source
    notes are untouched, so this is a genuine restore.
    """
    Meeting = apps.get_model("membership", "Meeting")
    Meeting.objects.filter(legacy_note_id__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0106_meeting_meetingagendaitem_meetingactionitem_and_more"),
    ]

    operations = [
        migrations.RunPython(migrate_meeting_notes, unmigrate_meeting_notes),
    ]
