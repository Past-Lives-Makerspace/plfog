"""Specs for core/management/commands/run_scheduled_tasks.py."""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


def describe_run_scheduled_tasks_command():
    def it_calls_all_scheduled_tasks(db):
        with (
            patch("core.management.commands.run_scheduled_tasks.call_command") as mock_cc,
        ):
            stdout = StringIO()
            call_command("run_scheduled_tasks", stdout=stdout)

        called_cmds = [c.args[0] for c in mock_cc.call_args_list]
        assert "send_voting_reminders" in called_cmds
        assert "send_lease_expiry_reminders" in called_cmds

    def it_continues_after_one_task_raises(db):
        """A failing task must not prevent later tasks from running."""

        def _fake_call(cmd, *a, **kw):
            if cmd == "send_voting_reminders":
                raise CommandError("boom")

        with patch("core.management.commands.run_scheduled_tasks.call_command", side_effect=_fake_call):
            stderr = StringIO()
            # Must not raise — dispatcher absorbs the error
            call_command("run_scheduled_tasks", stderr=stderr)

        assert "send_voting_reminders" in stderr.getvalue()

    def it_logs_each_task_outcome(db):
        with patch("core.management.commands.run_scheduled_tasks.call_command"):
            stdout = StringIO()
            call_command("run_scheduled_tasks", stdout=stdout)

        output = stdout.getvalue()
        assert output  # something was written

    def it_runs_sync_all_sources_at_13_utc(db):
        with (
            patch("core.management.commands.run_scheduled_tasks.call_command") as mock_cc,
            patch("core.management.commands.run_scheduled_tasks.timezone") as mock_tz,
        ):
            mock_now = MagicMock()
            mock_now.hour = 13
            mock_tz.now.return_value = mock_now
            call_command("run_scheduled_tasks")

        called_cmds = [c.args[0] for c in mock_cc.call_args_list]
        assert "sync_all_sources" in called_cmds

    def it_skips_sync_all_sources_outside_hour_13(db):
        with (
            patch("core.management.commands.run_scheduled_tasks.call_command") as mock_cc,
            patch("core.management.commands.run_scheduled_tasks.timezone") as mock_tz,
        ):
            mock_now = MagicMock()
            mock_now.hour = 9
            mock_tz.now.return_value = mock_now
            call_command("run_scheduled_tasks")

        called_cmds = [c.args[0] for c in mock_cc.call_args_list]
        assert "sync_all_sources" not in called_cmds
