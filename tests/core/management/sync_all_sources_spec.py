"""BDD specs for the sync_all_sources management command."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def describe_sync_all_sources_command():
    def it_reports_success_when_no_sources_fail():
        out = StringIO()
        with patch("hub.calendar_service.sync_all_sources", return_value=[]):
            call_command("sync_all_sources", stdout=out)
        assert "synced" in out.getvalue().lower()

    def it_raises_command_error_and_writes_each_failure():
        err = StringIO()
        with patch("hub.calendar_service.sync_all_sources", return_value=["legacy CMS: boom"]):
            with pytest.raises(CommandError, match="1 source"):
                call_command("sync_all_sources", stderr=err)
        assert "legacy CMS: boom" in err.getvalue()
