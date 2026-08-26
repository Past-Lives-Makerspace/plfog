"""Specs for the scheduled-job registry and its shared helpers (is_enabled, record_run)."""

from __future__ import annotations

import pytest
from django.core.management import get_commands

from core.factories import ScheduledJobStateFactory
from core.models import ScheduledTaskRun
from core.scheduled_jobs import (
    JOBS_BY_KEY,
    SCHEDULED_JOBS,
    Cadence,
    Trigger,
    is_enabled,
    record_run,
)

# The commands the dispatcher runs today (always-run + daily tuples), kept here as an
# independent copy so the parity test catches a job added to the registry but not wired,
# or a dispatcher job dropped from the registry.
_DISPATCHER_ALWAYS = {
    "send_voting_reminders",
    "take_cycle_snapshot",
    "send_lease_expiry_reminders",
    "auto_complete_orientations",
    "send_class_reminders",
    "publish_due_events",
    "send_event_reminders",
    "bill_tabs",
    "retry_calendar_pushes",
    "retry_discord_event_pushes",
    "sync_discord_guild_roles",
    "announce_calendar_events",
    "announce_new_classes",
    "sync_interested_rsvps",
}
_DISPATCHER_DAILY = {"sync_all_sources", "generate_orientation_slots", "sweep_stale_refunds"}
_DISPATCHER_WEEKLY = {"post_weekly_calendar_digest", "post_weekly_classes_digest"}


def describe_registry():
    def it_indexes_every_job_by_key():
        assert set(JOBS_BY_KEY) == {job.key for job in SCHEDULED_JOBS}

    def it_has_unique_keys():
        keys = [job.key for job in SCHEDULED_JOBS]
        assert len(keys) == len(set(keys))

    def it_only_uses_valid_cadences():
        valid = set(Cadence.values)
        assert all(job.cadence in valid for job in SCHEDULED_JOBS)

    def it_points_every_job_at_a_real_management_command():
        registered = set(get_commands())
        for job in SCHEDULED_JOBS:
            assert job.command in registered, f"{job.command} is not a registered management command"

    def it_matches_the_dispatcher_always_daily_and_weekly_tuples():
        dispatched = {
            job.key for job in SCHEDULED_JOBS if job.cadence in (Cadence.ALWAYS, Cadence.DAILY, Cadence.WEEKLY)
        }
        assert dispatched == _DISPATCHER_ALWAYS | _DISPATCHER_DAILY | _DISPATCHER_WEEKLY

    def it_keeps_the_airtable_pull_external():
        assert JOBS_BY_KEY["airtable_pull"].cadence == Cadence.EXTERNAL

    def it_marks_only_bill_tabs_as_a_money_job():
        money = {job.key for job in SCHEDULED_JOBS if job.money_job}
        assert money == {"bill_tabs"}

    def it_makes_the_money_job_non_toggleable():
        # Decision 2: bill_tabs is Run-now only (a hidden billing kill-switch would decouple
        # from BillingSettings), so it renders an "Always on" chip, not a toggle.
        assert JOBS_BY_KEY["bill_tabs"].toggleable is False

    def it_leaves_every_other_job_toggleable():
        for job in SCHEDULED_JOBS:
            if job.key != "bill_tabs":
                assert job.toggleable is True


def describe_is_enabled():
    def it_defaults_to_enabled_when_no_row_exists(db):
        assert is_enabled("send_class_reminders") is True

    def it_reflects_a_disabled_state_row(db):
        ScheduledJobStateFactory(task_key="send_class_reminders", enabled=False)
        assert is_enabled("send_class_reminders") is False

    def it_reflects_an_enabled_state_row(db):
        ScheduledJobStateFactory(task_key="send_class_reminders", enabled=True)
        assert is_enabled("send_class_reminders") is True


def describe_record_run():
    def it_records_a_clean_run_as_ok(db):
        with record_run("send_class_reminders", trigger=Trigger.SCHEDULED) as run:
            assert run.status == ScheduledTaskRun.Status.RUNNING
        run.refresh_from_db()
        assert run.succeeded
        assert run.finished_at is not None
        assert run.error == ""

    def it_records_the_trigger_and_actor(db, django_user_model):
        actor = django_user_model.objects.create(username="clicker")
        with record_run("bill_tabs", trigger=Trigger.MANUAL, actor=actor):
            pass
        run = ScheduledTaskRun.objects.get(task_key="bill_tabs")
        assert run.trigger == Trigger.MANUAL
        assert run.actor == actor

    def it_drops_an_actor_without_a_pk(db):
        from django.contrib.auth.models import AnonymousUser

        with record_run("bill_tabs", trigger=Trigger.MANUAL, actor=AnonymousUser()):
            pass
        run = ScheduledTaskRun.objects.get(task_key="bill_tabs")
        assert run.actor is None

    def describe_when_the_body_raises():
        def it_marks_failed_captures_the_error_and_reraises(db):
            with pytest.raises(ValueError, match="boom"):
                with record_run("sync_all_sources", trigger=Trigger.SCHEDULED):
                    raise ValueError("boom")
            run = ScheduledTaskRun.objects.get(task_key="sync_all_sources")
            assert run.failed
            assert run.error == "boom"
            assert run.finished_at is not None

    def describe_retention():
        def it_prunes_this_tasks_runs_older_than_the_window(db):
            from datetime import timedelta

            from django.utils import timezone

            from core.factories import ScheduledTaskRunFactory
            from core.scheduled_jobs import RUN_HISTORY_RETENTION_DAYS

            now = timezone.now()
            stale = ScheduledTaskRunFactory(
                task_key="send_class_reminders", started_at=now - timedelta(days=RUN_HISTORY_RETENTION_DAYS + 1)
            )
            recent = ScheduledTaskRunFactory(task_key="send_class_reminders", started_at=now - timedelta(days=1))
            with record_run("send_class_reminders", trigger=Trigger.SCHEDULED):
                pass
            assert not ScheduledTaskRun.objects.filter(pk=stale.pk).exists()
            assert ScheduledTaskRun.objects.filter(pk=recent.pk).exists()

        def it_only_prunes_the_running_tasks_own_history(db):
            from datetime import timedelta

            from django.utils import timezone

            from core.factories import ScheduledTaskRunFactory
            from core.scheduled_jobs import RUN_HISTORY_RETENTION_DAYS

            old = timezone.now() - timedelta(days=RUN_HISTORY_RETENTION_DAYS + 5)
            other = ScheduledTaskRunFactory(task_key="bill_tabs", started_at=old)
            with record_run("send_class_reminders", trigger=Trigger.SCHEDULED):
                pass
            assert ScheduledTaskRun.objects.filter(pk=other.pk).exists()

        def it_leaves_stale_history_in_place_when_the_run_fails(db):
            from datetime import timedelta

            from django.utils import timezone

            from core.factories import ScheduledTaskRunFactory
            from core.scheduled_jobs import RUN_HISTORY_RETENTION_DAYS

            old = timezone.now() - timedelta(days=RUN_HISTORY_RETENTION_DAYS + 5)
            stale = ScheduledTaskRunFactory(task_key="sync_all_sources", started_at=old)
            with pytest.raises(ValueError, match="nope"):
                with record_run("sync_all_sources", trigger=Trigger.SCHEDULED):
                    raise ValueError("nope")
            assert ScheduledTaskRun.objects.filter(pk=stale.pk).exists()
