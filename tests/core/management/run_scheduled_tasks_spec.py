"""BDD specs for the run_scheduled_tasks dispatcher (task wiring + time-gating)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from django.core.management import call_command

# The dispatcher now records each run through ``record_run`` (a ScheduledTaskRun row),
# so every dispatch touches the DB — even when ``call_command`` is mocked.
pytestmark = pytest.mark.django_db


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
            patch("core.management.commands.run_scheduled_tasks.timezone.now", return_value=datetime(2026, 1, 1, 9, 0)),
        ):
            call_command("run_scheduled_tasks")
        bill_calls = [c for c in cc.call_args_list if c.args[0] == "bill_tabs"]
        assert len(bill_calls) == 1
        assert "force" not in bill_calls[0].kwargs


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
