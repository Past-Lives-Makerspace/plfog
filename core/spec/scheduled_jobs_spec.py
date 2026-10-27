"""Specs for the scheduled-job registry and its shared helpers (is_enabled, record_run,
alert_unrecovered_failures)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.core.management import get_commands

from core.factories import ScheduledJobStateFactory, ScheduledTaskRunFactory
from core.models import ScheduledTaskRun
from core.scheduled_jobs import (
    JOBS_BY_KEY,
    SCHEDULED_JOBS,
    Cadence,
    Trigger,
    alert_unrecovered_failures,
    current_cadence,
    is_enabled,
    record_run,
)

# The commands the dispatcher runs today (always-run + daily tuples), kept here as an
# independent copy so the parity test catches a job added to the registry but not wired,
# or a dispatcher job dropped from the registry.
_DISPATCHER_ALWAYS = {
    "send_payouts",
    # Every tick, self-gated on 8:00 Pacific (#894): DAILY's UTC 13:00 hour drifts with DST.
    "send_staff_digests",
    "send_voting_reminders",
    "take_cycle_snapshot",
    "send_lease_expiry_reminders",
    "auto_complete_orientations",
    "send_class_reminders",
    "publish_due_events",
    "finish_event_announcements",
    "send_event_reminders",
    "send_tour_reminders",
    "bill_tabs",
    "retry_calendar_pushes",
    "retry_discord_event_pushes",
    "retry_eventbrite_pushes",
    "sync_discord_guild_roles",
    "announce_calendar_events",
    "announce_new_classes",
    "sync_interested_rsvps",
    "expire_orientation_payment_holds",
    "release_reservation_payment_holds",
    "release_abandoned_class_holds",
    "take_reconciliation_snapshot",
    "send_queued_announcements",
    "announce_live_requests",
    "fetch_video_thumbnails",
    "process_discord_removals",
}
_DISPATCHER_DAILY = {
    "sync_all_sources",
    "generate_orientation_slots",
    "sweep_stale_refunds",
    "send_overdue_review_alerts",
    "welcome_new_members",
    "send_wiki_guild_digest",
}
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

    def it_asks_before_running_the_welcome_job_by_hand():
        # "Run now" bypasses the enabled toggle by design, and this job sends mail that
        # cannot be recalled, so it must route through the confirm modal.
        assert JOBS_BY_KEY["welcome_new_members"].confirm_before_run is True

    def it_does_not_call_the_welcome_job_a_money_job():
        # money_job also renders a "charges cards" badge, which would be false here.
        assert JOBS_BY_KEY["welcome_new_members"].money_job is False

    def it_marks_only_the_two_jobs_that_move_money_as_money_jobs():
        money = {job.key for job in SCHEDULED_JOBS if job.money_job}
        assert money == {"bill_tabs", "send_payouts"}

    def it_makes_the_money_job_non_toggleable():
        # Decision 2: bill_tabs is Run-now only (a hidden billing kill-switch would decouple
        # from BillingSettings), so it renders an "Always on" chip, not a toggle.
        assert JOBS_BY_KEY["bill_tabs"].toggleable is False
        # send_payouts (#662) follows the same rule: BillingSettings.connect_enabled is its switch.
        assert JOBS_BY_KEY["send_payouts"].toggleable is False

    def it_pins_only_the_jobs_that_must_never_be_paused():
        # send_queued_announcements: the dispatcher skips a disabled job BEFORE it records a
        # run, so pausing this one would leave an admin's queued announcement unsent, with no
        # run record and nothing in the composer or the voting UI to show it had stalled.
        # finish_event_announcements: the same, for an event announcement a redeploy cut short.
        # send_staff_digests: review requests email only through it (#894), so pausing it
        # would leave reviewers with the bell alone.
        pinned = {job.key for job in SCHEDULED_JOBS if not job.toggleable}
        assert pinned == {
            "bill_tabs",
            "finish_event_announcements",
            "send_payouts",
            "send_queued_announcements",
            "send_staff_digests",
        }

    def it_no_longer_registers_or_ships_the_retired_results_email_queue():
        # Retired with the results announcement (October 2026): nothing may re-add the job
        # without its command, which is gone. Its ScheduledTaskRun history rows stay.
        assert "send_pending_funding_results" not in JOBS_BY_KEY
        assert "send_pending_funding_results" not in get_commands()
        assert JOBS_BY_KEY["send_queued_announcements"].cadence == Cadence.ALWAYS

    def it_marks_welcome_new_members_default_off():
        # The new-member welcome automation must ship OFF and be turned on deliberately.
        assert JOBS_BY_KEY["welcome_new_members"].default_enabled is False

    def it_ships_the_discord_removal_job_off():
        # It DMs and removes people from Discord; an admin turns it on from Automations (#898).
        assert JOBS_BY_KEY["process_discord_removals"].default_enabled is False

    def it_leaves_every_other_job_default_on():
        for job in SCHEDULED_JOBS:
            if job.key not in ("welcome_new_members", "process_discord_removals"):
                assert job.default_enabled is True


def describe_is_enabled():
    def it_defaults_to_enabled_when_no_row_exists(db):
        assert is_enabled("send_class_reminders") is True

    def it_defaults_to_disabled_for_a_default_off_job(db):
        # welcome_new_members declares default_enabled=False, so absence of a row means OFF.
        assert is_enabled("welcome_new_members") is False

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

        def it_alerts_the_webmasters_at_once_by_default(db):
            with patch("core.scheduled_jobs.alert_webmasters") as alert:
                with pytest.raises(ValueError, match="boom"):
                    with record_run("sync_all_sources", trigger=Trigger.SCHEDULED):
                        raise ValueError("boom")
            alert.assert_called_once()

        def it_holds_the_alert_but_still_records_the_failure_when_deferred(db):
            with patch("core.scheduled_jobs.alert_webmasters") as alert:
                with pytest.raises(ValueError, match="boom"):
                    with record_run("sync_all_sources", trigger=Trigger.SCHEDULED, defer_alert=True):
                        raise ValueError("boom")
            alert.assert_not_called()
            run = ScheduledTaskRun.objects.get(task_key="sync_all_sources")
            assert run.failed
            assert run.error == "boom"

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


def describe_frequency_choice():
    def it_offers_daily_and_weekly_for_the_removal_job_only():
        offering = [job.key for job in SCHEDULED_JOBS if job.cadence_choices]
        assert offering == ["process_discord_removals"]
        assert JOBS_BY_KEY["process_discord_removals"].cadence_choices == (Cadence.DAILY, Cadence.WEEKLY)

    def it_falls_back_to_the_jobs_own_cadence():
        job = JOBS_BY_KEY["process_discord_removals"]
        assert job.cadence_from({}) == Cadence.WEEKLY
        assert job.cadence_from({job.key: "daily"}) == Cadence.DAILY
        assert job.cadence_from({job.key: "always"}) == Cadence.WEEKLY

    @pytest.mark.django_db
    def it_reads_the_admins_pick_from_the_state_row():
        ScheduledJobStateFactory(task_key="process_discord_removals", cadence="daily")
        assert current_cadence("process_discord_removals") == Cadence.DAILY


def describe_alert_unrecovered_failures():
    WINDOW = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    WINDOW_END = WINDOW + timedelta(hours=1)
    KEYS = ["post_weekly_calendar_digest", "sync_all_sources"]

    def _run(
        key: str, minute: int, status: str, *, trigger: str = Trigger.SCHEDULED, error: str = ""
    ) -> ScheduledTaskRun:
        return ScheduledTaskRunFactory(
            task_key=key, status=status, trigger=trigger, started_at=WINDOW + timedelta(minutes=minute), error=error
        )

    def _alerted(keys: list[str] = KEYS) -> list[tuple[str, str]]:
        with patch("core.scheduled_jobs.alert_webmasters") as alert:
            returned = alert_unrecovered_failures(keys, WINDOW, WINDOW_END)
        alerted = [(call.args[0].task_key, call.args[0].error) for call in alert.call_args_list]
        assert [key for key, _error in alerted] == returned
        return alerted

    def it_alerts_the_last_failed_run_of_a_job_that_never_recovered(db):
        _run("post_weekly_calendar_digest", 1, ScheduledTaskRun.Status.FAILED, error="first")
        _run("post_weekly_calendar_digest", 46, ScheduledTaskRun.Status.FAILED, error="last")
        assert _alerted() == [("post_weekly_calendar_digest", "last")]

    def it_sends_nothing_for_a_job_a_retry_recovered(db):
        _run("post_weekly_calendar_digest", 1, ScheduledTaskRun.Status.FAILED, error="timed out")
        _run("post_weekly_calendar_digest", 16, ScheduledTaskRun.Status.OK)
        assert _alerted() == []

    def it_counts_a_run_now_success_after_the_window_as_recovered(db):
        _run("sync_all_sources", 1, ScheduledTaskRun.Status.FAILED, error="feed down")
        _run("sync_all_sources", 65, ScheduledTaskRun.Status.OK, trigger=Trigger.MANUAL)
        assert _alerted() == []

    def it_ignores_a_run_now_failure_which_alerted_at_once(db):
        _run("sync_all_sources", 20, ScheduledTaskRun.Status.FAILED, trigger=Trigger.MANUAL, error="clicked")
        assert _alerted() == []

    def it_ignores_failures_outside_the_window(db):
        _run("sync_all_sources", -60, ScheduledTaskRun.Status.FAILED, error="yesterday")
        _run("sync_all_sources", 61, ScheduledTaskRun.Status.FAILED, error="after")
        assert _alerted() == []

    def it_only_looks_at_the_keys_it_is_given(db):
        _run("sync_all_sources", 1, ScheduledTaskRun.Status.FAILED, error="feed down")
        assert _alerted(["post_weekly_calendar_digest"]) == []
        assert _alerted() == [("sync_all_sources", "feed down")]
