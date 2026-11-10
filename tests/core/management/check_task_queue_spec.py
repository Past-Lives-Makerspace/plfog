"""Specs for ``check_task_queue``: the 15 minute watch on the durable task queue (#1150)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from core.scheduled_jobs import JOBS_BY_KEY, Cadence
from core.tasks import ABANDONED_AFTER, error_message_lines

pytestmark = pytest.mark.django_db


def _row(
    status: str,
    *,
    enqueued_hours_ago: float = 0,
    started_hours_ago: float | None = None,
    run_after: datetime | None = None,
) -> DBTaskResult:
    now = timezone.now()
    row: DBTaskResult[..., object] = DBTaskResult(
        task_path="tests.core.tasks_spec.remember",
        args_kwargs={"args": [], "kwargs": {}},
        backend_name="default",
        status=status,
    )
    if run_after is not None:
        row.run_after = run_after
    row.save()  # an unset run_after becomes the package's "no delay" far future date
    # enqueued_at is auto_now_add, so the ages are set after the insert.
    DBTaskResult.objects.filter(pk=row.pk).update(
        enqueued_at=now - timedelta(hours=enqueued_hours_ago),
        started_at=None if started_hours_ago is None else now - timedelta(hours=started_hours_ago),
    )
    row.refresh_from_db()
    return row


def describe_registration():
    def it_runs_every_tick_from_the_dispatcher():
        job = JOBS_BY_KEY["check_task_queue"]
        assert job.command == "check_task_queue"
        assert job.cadence == Cadence.ALWAYS


def describe_check_task_queue():
    def it_passes_when_nothing_has_waited_or_run_an_hour():
        _row("READY", enqueued_hours_ago=0.5)
        _row("RUNNING", enqueued_hours_ago=0.5, started_hours_ago=0.5)

        call_command("check_task_queue")

    def it_fails_when_a_ready_task_has_waited_over_an_hour_for_a_worker():
        _row("READY", enqueued_hours_ago=2)

        with pytest.raises(CommandError, match=r"1 background task\(s\) waited over an hour"):
            call_command("check_task_queue")

    def describe_a_delayed_task():
        def it_counts_the_wait_from_when_it_was_due_not_when_it_was_enqueued():
            # Enqueued 3 hours ago to run 30 minutes ago: it has waited 30 minutes, not 3 hours.
            _row("READY", enqueued_hours_ago=3, run_after=timezone.now() - timedelta(minutes=30))

            call_command("check_task_queue")

        def it_fails_once_it_has_waited_over_an_hour_past_its_time():
            _row("READY", enqueued_hours_ago=3, run_after=timezone.now() - timedelta(hours=2))

            with pytest.raises(CommandError, match="waited over an hour"):
                call_command("check_task_queue")

    def it_fails_when_a_task_has_run_over_an_hour():
        _row("RUNNING", enqueued_hours_ago=2, started_hours_ago=2)

        with pytest.raises(CommandError, match=r"1 background task\(s\) have run over an hour"):
            call_command("check_task_queue")

    def describe_a_task_its_worker_abandoned():
        def it_marks_one_running_past_the_limit_failed_with_a_clear_message():
            hours = ABANDONED_AFTER / timedelta(hours=1) + 1
            row = _row("RUNNING", enqueued_hours_ago=hours, started_hours_ago=hours)

            call_command("check_task_queue")

            row.refresh_from_db()
            assert row.status == "FAILED"
            assert row.finished_at is not None
            assert row.exception_class_path == "core.tasks.TaskAbandonedError"
            [message] = error_message_lines(row.task_result.errors[-1])
            assert message.startswith("The worker stopped while this task was running")

        def it_leaves_the_queue_clean_for_the_next_run():
            hours = ABANDONED_AFTER / timedelta(hours=1) + 1
            _row("RUNNING", enqueued_hours_ago=hours, started_hours_ago=hours)

            call_command("check_task_queue")
            call_command("check_task_queue")

            assert not DBTaskResult.objects.running().exists()
