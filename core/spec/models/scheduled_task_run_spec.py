"""Specs for the ScheduledTaskRun model + manager (run history the dashboard reads)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.factories import ScheduledTaskRunFactory
from core.models import ScheduledTaskRun

pytestmark = pytest.mark.django_db


def describe_ScheduledTaskRun():
    def describe_status_properties():
        def it_reports_succeeded_for_an_ok_run():
            run = ScheduledTaskRunFactory(status=ScheduledTaskRun.Status.OK)
            assert run.succeeded is True
            assert run.failed is False

        def it_reports_failed_for_a_failed_run():
            run = ScheduledTaskRunFactory(status=ScheduledTaskRun.Status.FAILED)
            assert run.failed is True
            assert run.succeeded is False

    def describe_duration():
        def it_returns_the_wall_clock_span_when_finished():
            start = timezone.now() - timedelta(seconds=30)
            run = ScheduledTaskRunFactory(started_at=start, finished_at=start + timedelta(seconds=30))
            assert run.duration == timedelta(seconds=30)

        def it_returns_none_while_running():
            run = ScheduledTaskRunFactory(status=ScheduledTaskRun.Status.RUNNING, finished_at=None)
            assert run.duration is None

    def describe_is_stale_running():
        def it_is_true_for_a_run_stuck_running_over_an_hour():
            run = ScheduledTaskRunFactory(
                status=ScheduledTaskRun.Status.RUNNING,
                finished_at=None,
                started_at=timezone.now() - timedelta(hours=2),
            )
            assert run.is_stale_running is True

        def it_is_false_for_a_fresh_running_run():
            run = ScheduledTaskRunFactory(status=ScheduledTaskRun.Status.RUNNING, finished_at=None)
            assert run.is_stale_running is False

        def it_is_false_for_a_finished_run():
            run = ScheduledTaskRunFactory(
                status=ScheduledTaskRun.Status.OK,
                started_at=timezone.now() - timedelta(hours=2),
            )
            assert run.is_stale_running is False

    def describe_mark_ok():
        def it_stamps_finished_at_and_ok():
            run = ScheduledTaskRunFactory(status=ScheduledTaskRun.Status.RUNNING, finished_at=None)
            run.mark_ok()
            run.refresh_from_db()
            assert run.succeeded
            assert run.finished_at is not None

    def describe_mark_failed():
        def it_stamps_finished_at_failed_and_captures_the_error():
            run = ScheduledTaskRunFactory(status=ScheduledTaskRun.Status.RUNNING, finished_at=None)
            run.mark_failed(ValueError("nope"))
            run.refresh_from_db()
            assert run.failed
            assert run.error == "nope"
            assert run.finished_at is not None

    def describe_latest_per_task():
        def it_returns_the_most_recent_run_per_key():
            now = timezone.now()
            ScheduledTaskRunFactory(task_key="a", started_at=now - timedelta(hours=2))
            newest_a = ScheduledTaskRunFactory(task_key="a", started_at=now - timedelta(minutes=5))
            b = ScheduledTaskRunFactory(task_key="b", started_at=now - timedelta(minutes=1))
            latest = ScheduledTaskRun.objects.latest_per_task()
            assert latest["a"] == newest_a
            assert latest["b"] == b

        def it_returns_empty_when_nothing_has_run():
            assert ScheduledTaskRun.objects.latest_per_task() == {}

        def it_stays_a_single_query(django_assert_num_queries):
            ScheduledTaskRunFactory(task_key="a")
            ScheduledTaskRunFactory(task_key="b")
            ScheduledTaskRunFactory(task_key="c")
            with django_assert_num_queries(1):
                ScheduledTaskRun.objects.latest_per_task()

    def describe_str():
        def it_names_the_key_and_status():
            run = ScheduledTaskRunFactory(task_key="bill_tabs", status=ScheduledTaskRun.Status.OK)
            assert "bill_tabs" in str(run)
            assert "ok" in str(run)
