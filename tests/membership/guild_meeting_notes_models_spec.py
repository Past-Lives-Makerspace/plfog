"""BDD specs for GuildMeetingNote / GuildMeetingNoteAttachment, the Markdown render
helper, and the document validator."""

from __future__ import annotations

from datetime import date
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import IntegrityError

from core.validators import validate_document
from membership.markdown import render_markdown
from membership.models import GuildMeetingNoteAttachment
from tests.membership.factories import (
    GuildFactory,
    GuildMeetingNoteAttachmentFactory,
    GuildMeetingNoteFactory,
)


def describe_GuildMeetingNote():
    def describe_ordering():
        def it_returns_newest_meeting_first_then_newest_post(db):
            guild = GuildFactory()
            older = GuildMeetingNoteFactory(guild=guild, meeting_date=date(2026, 1, 1))
            newer = GuildMeetingNoteFactory(guild=guild, meeting_date=date(2026, 5, 1))
            ordered = list(guild.meeting_notes.all())
            assert ordered == [newer, older]

    def describe_str():
        def it_includes_title_guild_and_date(db):
            guild = GuildFactory(name="Ceramics")
            note = GuildMeetingNoteFactory(guild=guild, title="June meeting", meeting_date=date(2026, 6, 1))
            assert str(note) == "June meeting — Ceramics (2026-06-01)"

    def describe_body_html():
        def it_renders_markdown_to_sanitized_html(db):
            note = GuildMeetingNoteFactory(body="**bold**")
            assert "<strong>bold</strong>" in note.body_html


def describe_GuildMeetingNoteAttachment():
    def describe_xor_constraint():
        def it_rejects_both_file_and_url(db):
            note = GuildMeetingNoteFactory()
            with pytest.raises(IntegrityError):
                GuildMeetingNoteAttachment.objects.create(
                    note=note,
                    file=SimpleUploadedFile("a.pdf", b"%PDF-1.4", "application/pdf"),
                    url="https://example.com",
                )

        def it_rejects_neither_file_nor_url(db):
            note = GuildMeetingNoteFactory()
            with pytest.raises(IntegrityError):
                GuildMeetingNoteAttachment.objects.create(note=note, file="", url="")

        def it_allows_exactly_a_url(db):
            note = GuildMeetingNoteFactory()
            att = GuildMeetingNoteAttachment.objects.create(note=note, url="https://example.com")
            assert att.pk is not None

        def it_allows_exactly_a_file(db):
            note = GuildMeetingNoteFactory()
            att = GuildMeetingNoteAttachment.objects.create(
                note=note, file=SimpleUploadedFile("a.pdf", b"%PDF-1.4", "application/pdf")
            )
            assert att.pk is not None

    def describe_ordering():
        def it_orders_by_sort_order(db):
            note = GuildMeetingNoteFactory()
            second = GuildMeetingNoteAttachmentFactory(note=note, sort_order=2)
            first = GuildMeetingNoteAttachmentFactory(note=note, sort_order=1)
            assert list(note.attachments.all()) == [first, second]

    def describe_display_name():
        def it_prefers_the_label(db):
            att = GuildMeetingNoteAttachmentFactory(label="Agenda", url="https://example.com/x")
            assert att.display_name == "Agenda"

        def it_falls_back_to_the_file_base_name(db):
            att = GuildMeetingNoteAttachmentFactory(file_doc=True, label="")
            assert att.display_name.endswith(".pdf")

        def it_falls_back_to_the_url(db):
            att = GuildMeetingNoteAttachmentFactory(label="", url="https://example.com/doc")
            assert att.display_name == "https://example.com/doc"

    def describe_is_file_and_is_link():
        def it_reports_a_file_attachment(db):
            att = GuildMeetingNoteAttachmentFactory(file_doc=True)
            assert att.is_file is True
            assert att.is_link is False

        def it_reports_a_link_attachment(db):
            att = GuildMeetingNoteAttachmentFactory()
            assert att.is_link is True
            assert att.is_file is False

    def describe_str():
        def it_references_the_note_title(db):
            note = GuildMeetingNoteFactory(title="Agenda day")
            att = GuildMeetingNoteAttachmentFactory(note=note)
            assert str(att) == f"Attachment #{att.pk} for Agenda day"

    def describe_save():
        def it_deletes_the_old_file_when_the_file_is_replaced(db):
            note = GuildMeetingNoteFactory()
            att = GuildMeetingNoteAttachment.objects.create(
                note=note, file=SimpleUploadedFile("old.pdf", b"%PDF-old", "application/pdf")
            )
            old_name = att.file.name
            storage = att.file.storage
            assert storage.exists(old_name)
            att.file = SimpleUploadedFile("new.pdf", b"%PDF-new", "application/pdf")
            att.save()
            assert not storage.exists(old_name)


def describe_render_markdown():
    def it_returns_empty_for_blank_source():
        assert render_markdown("") == ""

    def it_renders_basic_markdown():
        out = render_markdown("**bold** and a [link](https://example.com)")
        assert "<strong>bold</strong>" in out
        assert "<a " in out

    def it_renders_lists():
        out = render_markdown("- one\n- two")
        assert "<ul>" in out
        assert "<li>one</li>" in out

    def it_strips_script_tags():
        out = render_markdown("hello <script>alert('x')</script> world")
        # bleach drops the <script> tag (so it can't execute) but keeps the inner
        # text as inert plain text — the security guarantee is "no executable tag".
        assert "<script>" not in out
        assert "</script>" not in out

    def it_strips_inline_event_handlers_and_styles():
        out = render_markdown('<p onclick="x()" style="color:red">hi</p>')
        assert "onclick" not in out
        assert "style" not in out

    def it_hardens_every_link():
        out = render_markdown("[x](https://example.com)")
        assert 'rel="noopener nofollow noreferrer"' in out
        assert 'target="_blank"' in out

    def it_strips_javascript_hrefs():
        out = render_markdown("[x](javascript:alert(1))")
        assert "javascript:alert" not in out


def describe_validate_document():
    def it_accepts_an_allowed_small_file():
        upload = SimpleUploadedFile("a.pdf", b"%PDF-1.4", "application/pdf")
        validate_document(upload)  # no exception

    def it_rejects_a_disallowed_extension():
        upload = SimpleUploadedFile("a.exe", b"MZ", "application/octet-stream")
        with pytest.raises(ValidationError):
            validate_document(upload)

    def it_rejects_an_oversize_file():
        upload = SimpleUploadedFile("a.pdf", b"%PDF", "application/pdf")
        with mock.patch.object(upload, "size", 26 * 1024 * 1024):
            with pytest.raises(ValidationError):
                validate_document(upload)
