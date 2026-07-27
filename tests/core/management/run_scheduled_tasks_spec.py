"""BDD specs for the run_scheduled_tasks dispatcher (task wiring + time-gating)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command

from core.factories import ScheduledJobStateFactory, ScheduledTaskRunFactory
from core.models import ScheduledTaskRun

# The dispatcher now records each run through ``record_run`` (a ScheduledTaskRun row),
# so every dispatch touches the DB — even when ``call_command`` is mocked.
pytestmark = pytest.mark.django_db


def _boom_on_voting(cmd, *args, **kwargs):
    """A ``call_command`` side effect that raises for ``send_voting_reminders`` only."""
    if cmd == "send_voting_reminders":
        raise RuntimeError("kaboom")


def _tasks_called(hour: int, side_effect=None, day: int = 1, minute: int = 0) -> list[str]:
    """Run the dispatcher at a fixed UTC time with ``call_command`` mocked; return the dispatched
    command names. Pass ``side_effect`` to make a wrapped command raise (failure-path tests).
    ``day`` picks the January 2026 date — 1 is a Thursday, 5 is a Monday (WEEKLY-gate tests).
    ``minute`` moves the tick within the hour (dedup-at-a-mid-hour-tick tests)."""
    with (
        patch("core.management.commands.run_scheduled_tasks.call_command", side_effect=side_effect) as cc,
        patch(
            "core.management.commands.run_scheduled_tasks.timezone.now",
            return_value=datetime(2026, 1, day, hour, minute, tzinfo=UTC),
        ),
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

    def it_auto_takes_the_cycle_snapshot_every_tick():
        called = _tasks_called(hour=9)
        assert "take_cycle_snapshot" in called

    def it_retries_calendar_pushes_every_tick():
        # Self-gating (no-op when Google sync is off), so it slots into the always-run tuple.
        called = _tasks_called(hour=9)
        assert "retry_calendar_pushes" in called

    def it_publishes_due_events_every_tick():
        # Idempotent (state-guarded), so the deferred-publish promotion runs every tick.
        called = _tasks_called(hour=9)
        assert "publish_due_events" in called

    def it_sends_event_reminders_every_tick():
        # Idempotent (per-(event, offset) period dedupe), so reminders run every tick.
        called = _tasks_called(hour=9)
        assert "send_event_reminders" in called

    def it_syncs_discord_guild_roles_every_tick():
        # Idempotent + no-op when unconfigured, so the reaction reconcile runs every tick.
        called = _tasks_called(hour=9)
        assert "sync_discord_guild_roles" in called

    def it_dispatches_bill_tabs_every_tick():
        # Decision 3: bill_tabs is wired into the always-run tuple (no --force) so
        # receipts + failed-charge retries run automatically. It self-gates inside
        # the command (advisory lock + _is_billing_time), so an always-run dispatch
        # is safe even outside the configured billing schedule.
        called = _tasks_called(hour=9)
        assert "bill_tabs" in called
        # And it must NOT be invoked with --force (the dispatcher relies on the
        # command's own schedule gate, never a forced run).
        with (
            patch("core.management.commands.run_scheduled_tasks.call_command") as cc,
            patch(
                "core.management.commands.run_scheduled_tasks.timezone.now",
                return_value=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
            ),
        ):
            call_command("run_scheduled_tasks")
        bill_calls = [c for c in cc.call_args_list if c.args[0] == "bill_tabs"]
        assert len(bill_calls) == 1
        assert "force" not in bill_calls[0].kwargs

    def it_skips_the_daily_jobs_outside_hour_13():
        called = _tasks_called(hour=9)
        assert "sync_all_sources" not in called
        assert "generate_orientation_slots" not in called

    def it_announces_calendar_events_every_tick():
        # Self-gating (no-op when calendar posts are off) + idempotent (stamps each
        # announced row), so the announcer slots into the always-run set.
        called = _tasks_called(hour=9)
        assert "announce_calendar_events" in called

    def it_announces_new_classes_every_tick():
        # Same shape as the calendar announcer: self-gating + stamp-idempotent.
        called = _tasks_called(hour=9)
        assert "announce_new_classes" in called

    def describe_weekly_cadence():
        def it_posts_the_weekly_digests_on_monday_at_1300_utc():
            called = _tasks_called(hour=13, day=5)  # Monday, Jan 5 2026
            assert "post_weekly_calendar_digest" in called
            assert "post_weekly_classes_digest" in called

        def it_skips_the_digests_on_monday_outside_hour_13():
            called = _tasks_called(hour=9, day=5)
            assert "post_weekly_calendar_digest" not in called
            assert "post_weekly_classes_digest" not in called

        def it_skips_the_digests_at_1300_on_a_non_monday():
            called = _tasks_called(hour=13, day=1)  # Thursday, Jan 1 2026
            assert "post_weekly_calendar_digest" not in called
            assert "post_weekly_classes_digest" not in called
            assert "sync_all_sources" in called  # the DAILY jobs still run at 13:xx

    def it_never_dispatches_the_external_airtable_pull():
        assert "airtable_pull" not in _tasks_called(hour=13)

    def describe_when_a_job_is_paused():
        def it_skips_the_disabled_job_but_runs_the_rest():
            ScheduledJobStateFactory(task_key="send_class_reminders", enabled=False)
            called = _tasks_called(hour=9)
            assert "send_class_reminders" not in called
            assert "send_voting_reminders" in called

        def it_records_no_run_for_the_disabled_job():
            ScheduledJobStateFactory(task_key="send_class_reminders", enabled=False)
            _tasks_called(hour=9)
            assert not ScheduledTaskRun.objects.filter(task_key="send_class_reminders").exists()

    def describe_run_recording():
        def it_records_a_scheduled_ok_run_per_dispatched_job():
            _tasks_called(hour=9)
            run = ScheduledTaskRun.objects.filter(task_key="send_voting_reminders").latest("started_at")
            assert run.succeeded
            assert run.trigger == "scheduled"
            assert run.actor is None

    def describe_when_a_command_raises():
        def it_continues_to_later_jobs():
            called = _tasks_called(hour=9, side_effect=_boom_on_voting)
            assert "send_lease_expiry_reminders" in called

        def it_records_the_failed_job_and_marks_the_rest_ok():
            _tasks_called(hour=9, side_effect=_boom_on_voting)
            failed = ScheduledTaskRun.objects.filter(task_key="send_voting_reminders").latest("started_at")
            ok = ScheduledTaskRun.objects.filter(task_key="send_lease_expiry_reminders").latest("started_at")
            assert failed.failed
            assert failed.error == "kaboom"
            assert ok.succeeded


def describe_bill_tabs_self_gates_when_dispatched():
    """End-to-end: a dispatcher-style (no --force) bill_tabs run no-ops outside
    billing time, even with a tab full of pending charges — the schedule gate
    inside the command is what protects against an unwanted charge."""

    def it_makes_no_charge_outside_billing_time(db):
        from datetime import date

        from billing.models import BillingSettings, TabCharge
        from tests.billing.factories import (
            BillingSettingsFactory,
            TabEntryFactory,
            TabFactory,
        )

        # MONTHLY billing configured for day 15; "today" is not day 15 for most
        # of the month, so the command must no-op. Pin via charge_day_of_month to
        # a day that is guaranteed not to be today (today + offset, wrapped).
        today = date.today().day
        not_today = (today % 28) + 1  # 1..28, never equal to `today`
        BillingSettingsFactory(
            charge_frequency=BillingSettings.ChargeFrequency.MONTHLY,
            charge_day_of_month=not_today,
        )
        from membership.models import Member

        tab = TabFactory(stripe_customer_id="cus_x", stripe_payment_method_id="pm_x")
        Member.objects.filter(pk=tab.member.pk).update(status=Member.Status.ACTIVE)
        TabEntryFactory(tab=tab)

        call_command("bill_tabs")  # no --force, as the dispatcher invokes it

        assert TabCharge.objects.count() == 0


def describe_window_deduplication():
    """DAILY/WEEKLY jobs run once per window, not once per 15-min tick inside the hour."""

    MONDAY_1300 = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)  # day=5 is a Monday
    THURSDAY_1300 = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)  # day=1 is a Thursday

    def it_skips_a_weekly_job_that_already_succeeded_this_window():
        ScheduledTaskRunFactory(
            task_key="post_weekly_classes_digest",
            status=ScheduledTaskRun.Status.OK,
            started_at=MONDAY_1300 + timedelta(minutes=1),
        )
        assert "post_weekly_classes_digest" not in _tasks_called(hour=13, day=5)

    def it_dedupes_at_a_mid_hour_tick_against_the_top_of_the_hour():
        # The :45 tick must still see the :05 success and skip — the window boundary is the
        # top of the hour, not `now`, which is the whole point of the ``.replace(minute=0)``.
        ScheduledTaskRunFactory(
            task_key="post_weekly_classes_digest",
            status=ScheduledTaskRun.Status.OK,
            started_at=MONDAY_1300 + timedelta(minutes=5),
        )
        assert "post_weekly_classes_digest" not in _tasks_called(hour=13, day=5, minute=45)

    def it_skips_a_daily_job_that_already_succeeded_this_window():
        ScheduledTaskRunFactory(
            task_key="sync_all_sources",
            status=ScheduledTaskRun.Status.OK,
            started_at=THURSDAY_1300 + timedelta(minutes=2),
        )
        assert "sync_all_sources" not in _tasks_called(hour=13, day=1)

    def it_still_runs_a_weekly_job_after_only_a_failed_run_this_window():
        # A FAILED run must not block a retry — this is what let the calendar digest
        # recover on a later tick once its Discord permission was fixed.
        ScheduledTaskRunFactory(
            task_key="post_weekly_classes_digest",
            status=ScheduledTaskRun.Status.FAILED,
            started_at=MONDAY_1300 + timedelta(minutes=1),
        )
        assert "post_weekly_classes_digest" in _tasks_called(hour=13, day=5)

    def it_still_runs_a_weekly_job_whose_only_success_was_a_prior_window():
        ScheduledTaskRunFactory(
            task_key="post_weekly_classes_digest",
            status=ScheduledTaskRun.Status.OK,
            started_at=MONDAY_1300 - timedelta(minutes=5),  # before the top of this hour
        )
        assert "post_weekly_classes_digest" in _tasks_called(hour=13, day=5)

    def it_does_not_dedupe_always_jobs():
        # ALWAYS jobs must run every tick even with a success logged this hour.
        ScheduledTaskRunFactory(
            task_key="auto_complete_orientations",
            status=ScheduledTaskRun.Status.OK,
            started_at=datetime(2026, 1, 1, 9, 1, tzinfo=UTC),
        )
        assert "auto_complete_orientations" in _tasks_called(hour=9, day=1)
