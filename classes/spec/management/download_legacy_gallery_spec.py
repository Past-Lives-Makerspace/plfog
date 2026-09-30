"""Specs for classes/management/commands/download_legacy_gallery.py."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from classes.import_service import GalleryImportResult, LegacyGalleryImportError

pytestmark = pytest.mark.django_db


def describe_download_legacy_gallery_command():
    def it_prints_the_run_summary():
        result = GalleryImportResult(created=3, downloaded=2, reused=1, over_cap=0, unmatched=4, failed=0)
        out = StringIO()
        with patch("classes.management.commands.download_legacy_gallery.sync_legacy_gallery", return_value=result):
            call_command("download_legacy_gallery", stdout=out)

        assert "Added 3 gallery photo(s): 2 downloaded, 1 re-used" in out.getvalue()
        assert "4 legacy class(es) have no offering here" in out.getvalue()

    def it_exits_non_zero_when_the_import_reports_failure():
        with (
            patch(
                "classes.management.commands.download_legacy_gallery.sync_legacy_gallery",
                side_effect=LegacyGalleryImportError("7 of 8 legacy gallery download(s) failed"),
            ),
            pytest.raises(CommandError, match="7 of 8"),
        ):
            call_command("download_legacy_gallery", stdout=StringIO())
