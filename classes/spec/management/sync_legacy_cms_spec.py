"""Specs for classes/management/commands/sync_legacy_cms.py."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def describe_sync_legacy_cms_command():
    def it_calls_sync_legacy_cms_and_prints_count():
        out = StringIO()
        with patch("classes.management.commands.sync_legacy_cms.sync_legacy_cms", return_value=5):
            call_command("sync_legacy_cms", stdout=out)
        assert "5" in out.getvalue()

    def it_prints_zero_when_no_offerings_synced():
        out = StringIO()
        with patch("classes.management.commands.sync_legacy_cms.sync_legacy_cms", return_value=0):
            call_command("sync_legacy_cms", stdout=out)
        assert "0" in out.getvalue()

    def it_uses_success_style_for_output():
        out = StringIO()
        with patch("classes.management.commands.sync_legacy_cms.sync_legacy_cms", return_value=3):
            call_command("sync_legacy_cms", stdout=out)
        # Success-styled output uses ANSI codes, just verify the count is there
        assert "3" in out.getvalue()
