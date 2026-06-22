"""BDD specs for the run_scheduled_tasks dispatcher (task wiring + time-gating)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from django.core.management import call_command


def _tasks_called(hour: int) -> list[str]:
    with (
        patch("core.management.commands.run_scheduled_tasks.call_command") as cc,
        patch("core.management.commands.run_scheduled_tasks.timezone.now", return_value=datetime(2026, 1, 1, hour, 0)),
    ):
        call_command("run_scheduled_tasks")
    return [c.args[0] for c in cc.call_args_list]


def describe_run_scheduled_tasks():
    def it_auto_completes_orientations_every_tick():
        called = _tasks_called(hour=9)
        assert "auto_complete_orientations" in called
        assert "generate_orientation_slots" not in called

    def it_generates_slots_at_1300_utc():
        called = _tasks_called(hour=13)
        assert "generate_orientation_slots" in called
        assert "sync_all_sources" in called
