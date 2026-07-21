"""Declared registry of every scheduled background job — one source of truth.

The dispatcher (``run_scheduled_tasks``) iterates this list instead of hard-coded
tuples, the standalone crons (``airtable_pull``) record through the same helper, and
the Site Settings → Automations dashboard renders it. Because all three read the same
registry, the admin list can never drift out of sync with what actually runs.

``is_enabled`` and ``record_run`` are the two shared helpers: the dispatcher and the
"Run now" view both gate on ``is_enabled`` and wrap each run in ``record_run`` so every
run — scheduled or manual — is recorded uniformly.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

RUN_HISTORY_RETENTION_DAYS = 90

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from core.models import ScheduledTaskRun


class Cadence(models.TextChoices):
    ALWAYS = "always", "Every 15 minutes"  # dispatcher, every tick
    DAILY = "daily", "Once daily (~6 AM PT)"  # dispatcher, only when UTC hour == 13
    WEEKLY = "weekly", "Weekly (Mon ~6 AM PT)"  # dispatcher, only Mondays when UTC hour == 13
    EXTERNAL = "external", "Own schedule"  # separate Render cron (airtable_pull)


class Trigger(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    MANUAL = "manual", "Run now"


@dataclass(frozen=True)
class ScheduledJob:
    """One row of the registry. ``key`` == the management command name, and is also the
    ``ScheduledTaskRun.task_key`` / ``ScheduledJobState.task_key`` this job records under."""

    key: str
    name: str
    description: str
    command: str
    schedule_label: str
    cadence: str
    toggleable: bool = True  # False → the dashboard shows a static "Always on" chip, no toggle
    money_job: bool = False  # True → "Run now" routes through a confirm modal; still never --force


SCHEDULED_JOBS: list[ScheduledJob] = [
    ScheduledJob(
        key="send_voting_reminders",
        name="Guild voting reminders",
        description="Nudges members to submit their guild votes before the window closes.",
        command="send_voting_reminders",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="take_cycle_snapshot",
        name="Funding cycle snapshots",
        description="Records the current funding-cycle vote tallies so results are preserved.",
        command="take_cycle_snapshot",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="send_lease_expiry_reminders",
        name="Lease-expiry reminders",
        description="Warns members whose studio lease is about to end.",
        command="send_lease_expiry_reminders",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="auto_complete_orientations",
        name="Auto-complete orientations",
        description="Marks past orientation bookings complete so members finish onboarding.",
        command="auto_complete_orientations",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="send_class_reminders",
        name="Class reminder emails",
        description="Reminds registrants about a class the day before it meets.",
        command="send_class_reminders",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="publish_due_events",
        name="Publish scheduled events",
        description="Makes scheduled events and announcements go live at their planned time.",
        command="publish_due_events",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="send_event_reminders",
        name="Event reminders",
        description="Reminds members about upcoming community events they can attend.",
        command="send_event_reminders",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="bill_tabs",
        name="Charge member tabs",
        description="Runs member-tab billing and sends receipts on scheduled billing days.",
        command="bill_tabs",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
        toggleable=False,
        money_job=True,
    ),
    ScheduledJob(
        key="retry_calendar_pushes",
        name="Retry Google Calendar pushes",
        description="Re-sends event updates to Google Calendar that didn't go through the first time.",
        command="retry_calendar_pushes",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="retry_discord_event_pushes",
        name="Retry Discord event pushes",
        description="Re-sends events to the Discord server's Events that didn't go through, and keeps repeating events current.",
        command="retry_discord_event_pushes",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="sync_discord_guild_roles",
        name="Sync Discord guild roles",
        description="Keeps Discord guild roles in step with members' guild membership.",
        command="sync_discord_guild_roles",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="announce_calendar_events",
        name="New-event Discord posts",
        description="Posts newly added calendar events and classes to the #public-calendar Discord channel.",
        command="announce_calendar_events",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="post_weekly_calendar_digest",
        name="Weekly calendar digest",
        description="Posts the coming week's calendar lineup to the #public-calendar Discord channel.",
        command="post_weekly_calendar_digest",
        schedule_label="Mon ~6 AM",
        cadence=Cadence.WEEKLY,
    ),
    ScheduledJob(
        key="sync_all_sources",
        name="Nightly calendar & class sync",
        description="Refreshes every calendar feed and imports classes overnight.",
        command="sync_all_sources",
        schedule_label="Nightly ~6 AM",
        cadence=Cadence.DAILY,
    ),
    ScheduledJob(
        key="generate_orientation_slots",
        name="Generate orientation slots",
        description="Opens the next batch of bookable orientation time slots.",
        command="generate_orientation_slots",
        schedule_label="Nightly ~6 AM",
        cadence=Cadence.DAILY,
    ),
    ScheduledJob(
        key="airtable_pull",
        name="Airtable member pull",
        description="Imports member and space updates from Airtable.",
        command="airtable_pull",
        schedule_label="Nightly ~3 AM",
        cadence=Cadence.EXTERNAL,
    ),
]

JOBS_BY_KEY: dict[str, ScheduledJob] = {job.key: job for job in SCHEDULED_JOBS}


def is_enabled(key: str) -> bool:
    """Whether a job is currently allowed to run. Defaults to ``True`` when no state row
    exists, so a fresh database preserves today's "everything runs" behavior. Manual
    "Run now" bypasses this — a manual override should run even a paused job."""
    from core.models import ScheduledJobState

    return ScheduledJobState.objects.is_enabled(key)


@contextlib.contextmanager
def record_run(
    key: str,
    *,
    trigger: str,
    actor: User | None = None,
) -> Iterator[ScheduledTaskRun]:
    """Open a ``ScheduledTaskRun`` (RUNNING), run the wrapped body, then mark it OK on a
    clean exit or FAILED (capturing the error) on an exception — and **re-raise**, so the
    dispatcher's per-task try/except still logs and continues. This is the single writer of
    ``ScheduledTaskRun``: the dispatcher, ``airtable_pull``, and the Run-now view all use it.
    """
    from core.models import ScheduledTaskRun

    run = ScheduledTaskRun.objects.create(
        task_key=key,
        trigger=trigger,
        actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
        status=ScheduledTaskRun.Status.RUNNING,
    )
    try:
        yield run
    except Exception as exc:
        run.mark_failed(exc)
        raise
    else:
        run.mark_ok()
        _prune_run_history(key)


def _prune_run_history(key: str) -> None:
    """Delete this task's run rows older than the retention window — self-pruning history.

    Keeps the append-only ``ScheduledTaskRun`` table bounded without a separate cron job or
    admin surface: every completed run trims its own key's stale rows past
    ``RUN_HISTORY_RETENTION_DAYS``.
    """
    from core.models import ScheduledTaskRun

    cutoff = timezone.now() - timedelta(days=RUN_HISTORY_RETENTION_DAYS)
    ScheduledTaskRun.objects.filter(task_key=key, started_at__lt=cutoff).delete()
