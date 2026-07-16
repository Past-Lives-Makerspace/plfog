"""Specs for the registry-driven run_scheduled_tasks dispatcher (gating + run recording)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from django.core.management import call_command

from core.factories import ScheduledJobStateFactory
from core.models import ScheduledTaskRun

pytestmark = pytest.mark.django_db


def _boom_on_voting(cmd, *args, **kwargs):
    """A ``call_command`` side effect that raises for ``send_voting_reminders`` only."""
    if cmd == "send_voting_reminders":
        raise RuntimeError("kaboom")


def _dispatch(hour: int, side_effect=None):
    """Run the dispatcher at a fixed UTC hour with ``call_command`` mocked. Returns the mock."""
    with (
        patch("core.management.commands.run_scheduled_tasks.call_command", side_effect=side_effect) as cc,
        patch(
            "core.management.commands.run_scheduled_tasks.timezone.now",
            return_value=datetime(2026, 1, 1, hour, 0),
        ),
    ):
        call_command("run_scheduled_tasks")
    return cc


def _commands(cc) -> list[str]:
    return [c.args[0] for c in cc.call_args_list]


def describe_run_scheduled_tasks():
    def it_runs_the_always_jobs_every_tick():
        called = _commands(_dispatch(hour=9))
        assert "send_voting_reminders" in called
        assert "send_class_reminders" in called
        assert "sync_discord_guild_roles" in called

    def it_skips_the_daily_jobs_outside_hour_13():
        called = _commands(_dispatch(hour=9))
        assert "sync_all_sources" not in called
        assert "generate_orientation_slots" not in called

    def it_runs_the_daily_jobs_at_hour_13():
        called = _commands(_dispatch(hour=13))
        assert "sync_all_sources" in called
        assert "generate_orientation_slots" in called

    def it_never_dispatches_the_external_airtable_pull():
        assert "airtable_pull" not in _commands(_dispatch(hour=13))

    def it_dispatches_bill_tabs_without_force():
        cc = _dispatch(hour=9)
        bill_calls = [c for c in cc.call_args_list if c.args[0] == "bill_tabs"]
        assert len(bill_calls) == 1
        assert "force" not in bill_calls[0].kwargs

    def describe_when_a_job_is_paused():
        def it_skips_the_disabled_job_but_runs_the_rest():
            ScheduledJobStateFactory(task_key="send_class_reminders", enabled=False)
            called = _commands(_dispatch(hour=9))
            assert "send_class_reminders" not in called
            assert "send_voting_reminders" in called

        def it_records_no_run_for_the_disabled_job():
            ScheduledJobStateFactory(task_key="send_class_reminders", enabled=False)
            _dispatch(hour=9)
            assert not ScheduledTaskRun.objects.filter(task_key="send_class_reminders").exists()

    def describe_run_recording():
        def it_records_a_scheduled_ok_run_per_dispatched_job():
            _dispatch(hour=9)
            run = ScheduledTaskRun.objects.filter(task_key="send_voting_reminders").latest("started_at")
            assert run.succeeded
            assert run.trigger == "scheduled"
            assert run.actor is None

    def describe_when_a_command_raises():
        def it_continues_to_later_jobs():
            called = _commands(_dispatch(hour=9, side_effect=_boom_on_voting))
            assert "send_lease_expiry_reminders" in called

        def it_records_the_failed_job_and_marks_the_rest_ok():
            _dispatch(hour=9, side_effect=_boom_on_voting)
            failed = ScheduledTaskRun.objects.filter(task_key="send_voting_reminders").latest("started_at")
            ok = ScheduledTaskRun.objects.filter(task_key="send_lease_expiry_reminders").latest("started_at")
            assert failed.failed
            assert failed.error == "kaboom"
            assert ok.succeeded
