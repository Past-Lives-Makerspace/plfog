"""Data-migration spec for 0112 — folding ``GuildMeetingNote`` rows into approved Meetings.

Uses Django's ``MigrationExecutor`` (the 0086 precedent) so fixtures are built with the
historical models at the pre-migration state. Each test restores the schema to head in a
``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

from datetime import date
from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "membership"
_BEFORE = "0111_meeting_meetingagendaitem_meetingactionitem_and_more"
_AFTER = "0112_migrate_meeting_notes"

_migration = import_module("membership.migrations.0112_migrate_meeting_notes")


def _migrate(target: str):
    """Migrate the membership app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_guild(apps, name: str):
    return apps.get_model(_APP, "Guild").objects.create(name=name, slug=name.lower().replace(" ", "-"))


def _make_note(apps, guild, **kwargs):
    return apps.get_model(_APP, "GuildMeetingNote").objects.create(guild_id=guild.pk, **kwargs)


@pytest.mark.django_db(transaction=True)
def describe_migration_0112_migrate_meeting_notes():
    def it_maps_notes_to_approved_meetings_with_rendered_markdown():
        try:
            apps = _migrate(_BEFORE)
            guild = _make_guild(apps, "Woodshop")
            author = apps.get_model("auth", "User").objects.create(username="secretary")
            monthly_note = _make_note(
                apps,
                guild,
                meeting_date=date(2026, 6, 12),
                title="June Monthly meeting",
                body="**Decisions** were made\n\n<script>alert(1)</script>",
                created_by_id=author.pk,
            )
            special_note = _make_note(
                apps,
                guild,
                meeting_date=date(2026, 7, 3),
                title="Budget emergency",
                body="",
            )

            apps = _migrate(_AFTER)
            Meeting = apps.get_model(_APP, "Meeting")

            monthly = Meeting.objects.get(legacy_note_id=monthly_note.pk)
            assert monthly.status == "approved"
            assert monthly.guild_id == guild.pk
            assert monthly.scheduled_date == date(2026, 6, 12)
            assert monthly.is_special is False
            assert monthly.special_title == ""
            assert monthly.special_notes == ""
            # Markdown rendered and sanitized: bold survives; the script tag is stripped
            # (bleach keeps the now-inert inner text, drops the executable tag).
            assert "<strong>Decisions</strong>" in monthly.other_notes
            assert "<script>" not in monthly.other_notes
            # Approval stamps come from the note's author and post date.
            assert monthly.approved_at == monthly_note.created_at
            assert monthly.approved_by_id == author.pk
            assert monthly.created_by_id == author.pk

            # A non-"monthly" title becomes a named Special meeting carrying the headline.
            special = Meeting.objects.get(legacy_note_id=special_note.pk)
            assert special.is_special is True
            assert special.special_title == "Budget emergency"
            assert special.other_notes == ""
            assert special.approved_by_id is None
            assert special.created_by_id is None
        finally:
            _migrate(_AFTER)

    def it_copies_attachments_pointing_at_the_same_stored_files():
        try:
            apps = _migrate(_BEFORE)
            guild = _make_guild(apps, "Textiles")
            note = _make_note(apps, guild, meeting_date=date(2026, 5, 1), title="May Monthly meeting")
            Attachment = apps.get_model(_APP, "GuildMeetingNoteAttachment")
            # Assigning the stored name directly writes no file — mirroring the migration.
            Attachment.objects.create(
                note=note, label="Agenda PDF", file="guilds/meeting_notes/agenda.pdf", sort_order=1
            )
            Attachment.objects.create(note=note, url="https://docs.example/minutes", sort_order=2)

            apps = _migrate(_AFTER)
            meeting = apps.get_model(_APP, "Meeting").objects.get(legacy_note_id=note.pk)
            rows = list(
                apps.get_model(_APP, "MeetingAttachment").objects.filter(meeting_id=meeting.pk).order_by("sort_order")
            )
            assert [row.label for row in rows] == ["Agenda PDF", ""]
            assert rows[0].file.name == "guilds/meeting_notes/agenda.pdf"  # the same stored file, not a copy
            assert rows[0].url == ""
            assert rows[1].file.name == ""
            assert rows[1].url == "https://docs.example/minutes"
            assert [row.sort_order for row in rows] == [1, 2]
        finally:
            _migrate(_AFTER)

    def it_is_idempotent_and_a_re_run_creates_nothing():
        try:
            apps = _migrate(_BEFORE)
            guild = _make_guild(apps, "Metals")
            _make_note(apps, guild, meeting_date=date(2026, 4, 10), title="April Monthly meeting")

            apps = _migrate(_AFTER)
            Meeting = apps.get_model(_APP, "Meeting")
            before_count = Meeting.objects.count()

            _migration.migrate_meeting_notes(apps, None)

            assert Meeting.objects.count() == before_count
        finally:
            _migrate(_AFTER)

    def it_reverse_deletes_only_migrated_meetings_and_leaves_sources_untouched():
        try:
            apps = _migrate(_BEFORE)
            guild = _make_guild(apps, "Ceramics")
            note = _make_note(apps, guild, meeting_date=date(2026, 3, 6), title="March Monthly meeting")
            apps.get_model(_APP, "GuildMeetingNoteAttachment").objects.create(
                note=note, file="guilds/meeting_notes/kiln-budget.xlsx", sort_order=1
            )

            apps = _migrate(_AFTER)
            Meeting = apps.get_model(_APP, "Meeting")
            hand_made = Meeting.objects.create(guild_id=guild.pk, status="draft")
            assert Meeting.objects.count() == 2

            apps = _migrate(_BEFORE)  # unapplies 0112 — the real reverse runs
            Meeting = apps.get_model(_APP, "Meeting")
            assert list(Meeting.objects.values_list("pk", flat=True)) == [hand_made.pk]
            assert apps.get_model(_APP, "MeetingAttachment").objects.count() == 0
            # The source note and its attachment row survive intact.
            assert apps.get_model(_APP, "GuildMeetingNote").objects.filter(pk=note.pk).exists()
            source_attachments = apps.get_model(_APP, "GuildMeetingNoteAttachment").objects.filter(note_id=note.pk)
            assert source_attachments.count() == 1
        finally:
            _migrate(_AFTER)
