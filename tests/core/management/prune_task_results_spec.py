"""Specs for ``prune_task_results``: the daily tending of the durable task queue (#1150)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from core.scheduled_jobs import JOBS_BY_KEY, Cadence

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _database_backend(settings):
    # The package's prune command works only on the database backend, as production runs.
    settings.TASKS = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}


def _result(status: str, *, days_ago: float = 0, hours_ago: float = 0, queue: str = "default") -> DBTaskResult:
    when = timezone.now() - timedelta(days=days_ago, hours=hours_ago)
    row = DBTaskResult.objects.create(
        task_path="tests.core.tasks_spec.remember",
        args_kwargs={"args": [], "kwargs": {}},
        backend_name="default",
        queue_name=queue,
        status=status,
    )
    # enqueued_at is auto_now_add, so the age is set after the insert.
    DBTaskResult.objects.filter(pk=row.pk).update(
        enqueued_at=when, started_at=when if status != "READY" else None, finished_at=when
    )
    return row


def _left() -> set[str]:
    return {str(pk) for pk in DBTaskResult.objects.values_list("pk", flat=True)}


def describe_registration():
    def it_runs_daily_from_the_dispatcher():
        job = JOBS_BY_KEY["prune_task_results"]
        assert job.command == "prune_task_results"
        assert job.cadence == Cadence.DAILY


def describe_prune_task_results():
    def it_keeps_successes_30_days_and_failures_90_on_every_queue():
        old_success = _result("SUCCESSFUL", days_ago=31, queue="emails")
        recent_success = _result("SUCCESSFUL", days_ago=29)
        old_failure = _result("FAILED", days_ago=91)
        recent_failure = _result("FAILED", days_ago=60, queue="emails")

        call_command("prune_task_results")

        assert _left() == {str(recent_success.pk), str(recent_failure.pk)}
        assert str(old_success.pk) not in _left()
        assert str(old_failure.pk) not in _left()

    def it_passes_when_nothing_is_stuck():
        _result("READY", hours_ago=0.5)
        _result("RUNNING", hours_ago=0.5)

        call_command("prune_task_results")

    def it_fails_when_a_task_has_waited_over_an_hour_for_a_worker():
        _result("READY", hours_ago=2)

        with pytest.raises(CommandError, match="1 background task\\(s\\) waited over an hour"):
            call_command("prune_task_results")

    def it_fails_when_a_task_has_run_over_an_hour():
        _result("RUNNING", hours_ago=2)

        with pytest.raises(CommandError, match="1 background task\\(s\\) have run over an hour"):
            call_command("prune_task_results")
