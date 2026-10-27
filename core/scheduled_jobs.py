"""Declared registry of every scheduled background job — one source of truth.

The dispatcher (``run_scheduled_tasks``) iterates this list instead of hard-coded
tuples, the standalone crons (``airtable_pull``) record through the same helper, and
the Automations page renders it. Because all three read the same
registry, the admin list can never drift out of sync with what actually runs.

``is_enabled`` and ``record_run`` are the two shared helpers. The dispatcher gates on
``is_enabled``; the "Run now" view deliberately does NOT, because a manual override
should run even a paused job. Both wrap each run in ``record_run``, so every run —
scheduled or manual — is recorded uniformly.

A failure alerts the Webmasters at once, except a scheduled DAILY or WEEKLY run: its
window retries it, so the dispatcher holds that alert and sends it after the window only
if no retry recovered (``alert_unrecovered_failures``, #933).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

RUN_HISTORY_RETENTION_DAYS = 90

# How much of a failed run's error line the Webmaster alert quotes (push trims it further).
ALERT_ERROR_LIMIT = 300

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
    COMMAND = "command", "Command line"  # a one-time management command run by hand (#898)


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
    # True → same confirm modal, for a job that is irreversible but does not touch money.
    # Kept separate from money_job because that flag also renders a "charges cards" badge,
    # and labelling an email job that way would be false to the admin reading it.
    confirm_before_run: bool = False
    default_enabled: bool = True  # False → OFF until an admin turns it on; absence of a state row means OFF, not ON
    # The frequencies an admin may pick on Automations. Empty → the schedule is fixed at ``cadence``.
    cadence_choices: tuple[str, ...] = ()

    def cadence_from(self, chosen: dict[str, str]) -> str:
        """The frequency this job runs at: the admin's pick from ``chosen`` when it is one this
        job offers, else its own ``cadence``."""
        pick = chosen.get(self.key, "")
        return pick if pick in self.cadence_choices else self.cadence


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
        description=(
            "Records each month's vote tallies so results are preserved, and makes that month's voting results draft."
        ),
        command="take_cycle_snapshot",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="send_queued_announcements",
        name="Queued announcements",
        description="Sends the site-wide announcements queued from the composer, such as the monthly voting results.",
        command="send_queued_announcements",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
        # Not toggleable. The dispatcher skips a disabled job before it records a run, so
        # pausing this one would leave an admin's queued announcement never sent, with no
        # run record and nothing in the composer or the voting UI to show it had stalled.
        toggleable=False,
    ),
    ScheduledJob(
        key="send_staff_digests",
        name="Staff request digest",
        description=("Sends each reviewer one email at 8 AM listing the class review requests still waiting on them."),
        command="send_staff_digests",
        schedule_label="Every 15 min (sends from 8 AM)",
        cadence=Cadence.ALWAYS,
        # Not toggleable: review requests no longer email on their own (#894), so pausing
        # this would leave reviewers with the bell alone and nothing saying so.
        toggleable=False,
    ),
    ScheduledJob(
        key="take_reconciliation_snapshot",
        name="Reconciliation month-end snapshots",
        description="Freezes the prior month's per-recipient payout allocation once, at month end.",
        command="take_reconciliation_snapshot",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="send_lease_expiry_reminders",
        name="Space agreement expiry reminders",
        description="Warns members whose space agreement is about to end.",
        command="send_lease_expiry_reminders",
        schedule_label="Every 15 min (sends from 9 AM)",
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
        key="finish_event_announcements",
        name="Finish event announcements",
        description=(
            "Finishes telling members about a newly published event when the send was interrupted, "
            "such as by a redeploy. Nobody is told twice."
        ),
        command="finish_event_announcements",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
        # Not toggleable, for the reason send_queued_announcements is not: paused, an
        # interrupted event announcement would stay unsent with no run record to show it.
        toggleable=False,
    ),
    ScheduledJob(
        key="send_event_reminders",
        name="Event reminders",
        description="Reminds members about upcoming events they can attend.",
        command="send_event_reminders",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="send_payouts",
        name="Send instructor and orientor payouts",
        description=(
            "Records each instructor and orientor share as it falls due and sends it through Stripe. "
            "Idle while payouts are off in Payments, Stripe tab."
        ),
        command="send_payouts",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
        # Like bill_tabs: BillingSettings.connect_enabled is the switch, so no second, hidden one here.
        toggleable=False,
        money_job=True,
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
        key="retry_eventbrite_pushes",
        name="Retry Eventbrite pushes",
        description="Re-sends class listings to Eventbrite that didn't go through the first time.",
        command="retry_eventbrite_pushes",
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
        description="Posts newly added calendar events and classes to the #calendar Discord channel.",
        command="announce_calendar_events",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="post_weekly_calendar_digest",
        name="Weekly calendar digest",
        description="Posts the coming week's calendar lineup to the #calendar Discord channel.",
        command="post_weekly_calendar_digest",
        schedule_label="Mon ~6 AM",
        cadence=Cadence.WEEKLY,
    ),
    ScheduledJob(
        key="announce_new_classes",
        name="New-class Discord posts",
        description="Posts newly published classes to the Discord #classes channel.",
        command="announce_new_classes",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="post_weekly_classes_digest",
        name="Weekly classes digest",
        description="Posts the coming week's class lineup to the Discord #classes channel.",
        command="post_weekly_classes_digest",
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
        key="welcome_new_members",
        name="Welcome emails for new members",
        description=(
            "Emails new paying members their sign-in link once they're active in Airtable, so they know "
            "their account is ready. Off by default."
        ),
        command="welcome_new_members",
        schedule_label="Daily ~6 AM",
        cadence=Cadence.DAILY,
        default_enabled=False,
        # "Run now" bypasses the enabled toggle by design, and this job sends mail that
        # cannot be recalled, so it asks first.
        confirm_before_run=True,
    ),
    ScheduledJob(
        key="expire_orientation_payment_holds",
        name="Release abandoned orientation checkouts",
        description="Releases orientation seats held by checkouts that were never completed.",
        command="expire_orientation_payment_holds",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="release_reservation_payment_holds",
        name="Release abandoned reservation checkouts",
        description="Releases equipment time held by paid reservations whose checkout was never completed.",
        command="release_reservation_payment_holds",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="release_abandoned_class_holds",
        name="Release abandoned class checkouts",
        description="Releases class seats held by signups whose Stripe checkout was never completed.",
        command="release_abandoned_class_holds",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="airtable_pull",
        name="Airtable member pull",
        description="Imports member and space updates from Airtable.",
        command="airtable_pull",
        schedule_label="Nightly ~3 AM",
        cadence=Cadence.EXTERNAL,
    ),
    ScheduledJob(
        key="sync_interested_rsvps",
        name="Discord Interested sync",
        description="Folds Interested marks on Discord server events into the event RSVP lists.",
        command="sync_interested_rsvps",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="sweep_stale_refunds",
        name="Sweep stale refunds",
        description="Marks refunds that never reached Stripe as failed so they can be retried.",
        command="sweep_stale_refunds",
        schedule_label="Nightly ~6 AM",
        cadence=Cadence.DAILY,
    ),
    ScheduledJob(
        key="send_wiki_guild_digest",
        name="Guild wiki digest",
        description=(
            "Emails each guild's leadership a monthly summary of their wiki, and prunes old "
            "search-miss rows. The digest only goes out on the 1st; the prune runs daily."
        ),
        command="send_wiki_guild_digest",
        schedule_label="Daily (digest on the 1st)",
        cadence=Cadence.DAILY,
    ),
    ScheduledJob(
        key="send_overdue_review_alerts",
        name="Overdue class reviews",
        description=(
            "Posts each class review that has waited 3 days or more to the member portal alerts "
            "channel on Discord, again every 3 days while it waits."
        ),
        command="send_overdue_review_alerts",
        schedule_label="Nightly ~6 AM",
        cadence=Cadence.DAILY,
    ),
    # Must stay after sync_all_sources: daily jobs run in list order, and the check reads the
    # Google rows that import brings in (#953).
    ScheduledJob(
        key="check_needs_attention",
        name="Guild needs attention",
        description=(
            "Checks every active guild for things its staff should have in order, such as something "
            "coming up, and puts an inbox item in front of its leads and staff when one is missing."
        ),
        command="check_needs_attention",
        schedule_label="Daily ~6 AM",
        cadence=Cadence.DAILY,
    ),
    ScheduledJob(
        key="announce_live_requests",
        name="Requests gone live",
        description=(
            "Once a release that lists feedback requests is serving, marks each one Live and tells "
            "the member who asked, once."
        ),
        command="announce_live_requests",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="fetch_video_thumbnails",
        name="Instagram video pictures",
        description=(
            "Copies the post picture for each class with an Instagram video link, so the class page "
            "shows it. A post that cannot be fetched is tried again a day later."
        ),
        command="fetch_video_thumbnails",
        schedule_label="Every 15 min",
        cadence=Cadence.ALWAYS,
    ),
    ScheduledJob(
        key="process_discord_removals",
        name="Remove ex-members from Discord",
        description=(
            "Warns members whose membership ended by Discord DM, and removes them from the Discord "
            "on the next run unless they are active again. Off by default."
        ),
        command="process_discord_removals",
        schedule_label="Weekly",
        cadence=Cadence.WEEKLY,
        cadence_choices=(Cadence.DAILY, Cadence.WEEKLY),
        default_enabled=False,
    ),
]

JOBS_BY_KEY: dict[str, ScheduledJob] = {job.key: job for job in SCHEDULED_JOBS}


def is_enabled(key: str) -> bool:
    """Whether a job is currently allowed to run.

    With no state row the answer is the job's own ``default_enabled``, so a job that ships
    off stays off on a fresh database instead of firing on the next deploy. Manual
    "Run now" bypasses this — a manual override should run even a paused job."""
    from core.models import ScheduledJobState

    return ScheduledJobState.objects.is_enabled(key)


def current_cadence(key: str) -> str:
    """The frequency a registry job runs at right now, with an admin's pick applied."""
    from core.models import ScheduledJobState

    return JOBS_BY_KEY[key].cadence_from(ScheduledJobState.objects.cadences())


def has_succeeded_since(key: str, since: datetime) -> bool:
    """Whether a job already completed OK at or after ``since``.

    The dispatcher uses this to make DAILY/WEEKLY jobs run once per window instead of once
    per 15-minute tick: those cadences qualify for a whole UTC hour, so the cron fires them
    ~4 times each window otherwise (the duplicate #classes digests were this bug). A FAILED
    run does not count, so a transient failure still retries within the same window — which
    is exactly how a job recovers mid-window once its underlying cause is fixed.
    """
    from core.models import ScheduledTaskRun

    return ScheduledTaskRun.objects.filter(
        task_key=key, status=ScheduledTaskRun.Status.OK, started_at__gte=since
    ).exists()


@contextlib.contextmanager
def record_run(
    key: str,
    *,
    trigger: str,
    actor: User | None = None,
    defer_alert: bool = False,
) -> Iterator[ScheduledTaskRun]:
    """Open a ``ScheduledTaskRun`` (RUNNING), run the wrapped body, then mark it OK on a
    clean exit or FAILED (capturing the error) on an exception — and **re-raise**, so the
    dispatcher's per-task try/except still logs and continues. This is the single writer of
    ``ScheduledTaskRun``: the dispatcher, ``airtable_pull``, and the Run-now view all use it.

    A failure also alerts the Webmasters (:func:`alert_webmasters`) before the re-raise, for
    every trigger, unless ``defer_alert`` is set. The dispatcher sets it for a DAILY or WEEKLY
    run, whose failure a later tick in the window retries; the window's end decides that
    alert instead (:func:`alert_unrecovered_failures`). The alert never raises, so the job's
    own exception is what propagates.
    """
    from core.models import ScheduledTaskRun

    run = ScheduledTaskRun.objects.create(
        task_key=key,
        trigger=trigger,
        actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
        status=ScheduledTaskRun.Status.RUNNING,
        # The field default holds the original timezone.now, so a spec patching that name pins every clock but this one.
        started_at=timezone.now(),
    )
    token = _current_run.set(run)
    try:
        yield run
    except Exception as exc:
        run.mark_failed(exc)
        if not defer_alert:
            alert_webmasters(run, exc)
        raise
    else:
        run.mark_ok()
        _prune_run_history(key)
    finally:
        _current_run.reset(token)


# The run ``record_run`` has open, so an action the wrapped command takes can name its run
# (``AutomationAction.record``) although ``call_command`` hands the command no run object.
_current_run: ContextVar[ScheduledTaskRun | None] = ContextVar("current_scheduled_run", default=None)


def current_run() -> ScheduledTaskRun | None:
    """The run ``record_run`` currently has open in this context, or ``None`` outside one."""
    return _current_run.get()


def automations_path() -> str:
    """The Automations page (#922), where every job's run history lives."""
    from django.urls import reverse

    return reverse("hub_admin_automations")


def alert_webmasters(run: ScheduledTaskRun, exc: Exception | None = None) -> None:
    """Tell the Webmasters that ``run`` failed: at most one alert per job per Pacific day.

    The ``automation.failed`` event reaches the WEBMASTER capability holders only. Its
    period is the job key plus the Pacific date of the failure, so the delivery ledger
    lets one alert through per job, per person, per day: a job failing every 15 minutes
    alerts once, and two jobs failing the same day alert twice.

    The alert has to survive the kind of failure it reports. The incident behind it was an
    email template that could not render in the cron, so the alert carries plain text with
    no template (``html_body`` stays ``None``, so nothing loads ``{% static %}``), and it goes
    out in two passes: the bell and push for every Webmaster first, then the email. Within
    one ``emit`` a raising channel stops the rest of that person's channels and every
    later person, so a failing email would otherwise cost the push and the other
    Webmasters their alerts. The bell and push slots are already claimed when the email
    pass runs, so it sends the email alone. An email that fails releases its slot and is
    retried on the job's next alert that day: its next failure, or for a held DAILY or WEEKLY
    alert the next tick after its window.

    ``exc`` is ``None`` when the alert comes after the window, from the run row alone; it only
    names the exception type when the run's error is empty.

    Never raises: each pass logs its own failure with ``logger.exception``.
    """
    _alert_pass(run, exc, suppress_email=True)
    _alert_pass(run, exc, suppress_push=True)


def _alert_pass(
    run: ScheduledTaskRun, exc: Exception | None, *, suppress_email: bool = False, suppress_push: bool = False
) -> None:
    """One emit of the failure alert, logging instead of raising (see :func:`alert_webmasters`)."""
    from django.conf import settings
    from django.utils.formats import date_format
    from django.utils.text import Truncator

    from core.events.channels import Message
    from core.events.emit import emit
    from core.events.registry import AUTOMATION_FAILED, Channel

    try:
        # .get, not [key]: a KeyError here would be swallowed by the except below and cost the
        # whole alert, so a key the registry no longer knows is named by its key instead.
        job = JOBS_BY_KEY.get(run.task_key)
        title = f"{job.name if job is not None else run.task_key} failed"
        failed_at = timezone.localtime(run.finished_at)
        error_lines = [line.strip() for line in run.error.splitlines() if line.strip()]
        # run.error is str(exc), not a traceback, so the headline is the FIRST line. Later lines are
        # detail that must stay out of an inbox and a push tray: psycopg's "DETAIL: Key (email)=(...)"
        # carries member data, and httpx ends with a "For more information check: <url>" footer.
        fallback = type(exc).__name__ if exc is not None else "no message"
        error = Truncator(error_lines[0]).chars(ALERT_ERROR_LIMIT) if error_lines else fallback
        when = date_format(failed_at, r"l, F j \a\t g:i A")  # Saturday, October 3 at 6:15 AM
        body = "\n".join(
            [
                f"It failed on {when} Pacific time.",
                f"Error: {error}",
                "You will get at most one alert a day for this automation.",
            ]
        )
        path = automations_path()
        link = f"{settings.MEMBER_BASE_URL.rstrip('/')}{path}"
        email = Message(
            title=title,
            body=f"{body}\n\nSee the run history on the Automations page: {link}",
            url=link,
            trigger_kind=AUTOMATION_FAILED,
        )
        emit(
            AUTOMATION_FAILED,
            context={},
            title=title,
            body=body,
            url=path,
            period=f"{AUTOMATION_FAILED}:{run.task_key}:{failed_at:%Y-%m-%d}",
            messages={Channel.EMAIL: email},
            suppress_email=suppress_email,
            suppress_push=suppress_push,
        )
    except Exception:
        logger.exception(
            "Could not send the Webmasters the %s alert that the %s automation failed",
            "email" if suppress_push else "bell and push",
            run.task_key,
        )


def alert_unrecovered_failures(keys: list[str], since: datetime, until: datetime) -> list[str]:
    """Alert the Webmasters for each job in ``keys`` whose scheduled runs failed in the window
    ``since`` to ``until`` and that has no OK run since ``since``; return the keys alerted.

    The dispatcher calls this after the DAILY/WEEKLY window, on each tick of the hour after it
    (#933). A failure a later tick recovered stays silent; one that never recovered alerts with
    the window's last failed run, so the alert names the last error. The repeat ticks send
    nothing new, because :func:`alert_webmasters` lets one alert per job per Pacific day through.
    """
    from core.models import ScheduledTaskRun

    recovered = set(
        ScheduledTaskRun.objects.filter(
            task_key__in=keys, status=ScheduledTaskRun.Status.OK, started_at__gte=since
        ).values_list("task_key", flat=True)
    )
    failures = (
        ScheduledTaskRun.objects.filter(
            task_key__in=keys,
            trigger=Trigger.SCHEDULED,
            status=ScheduledTaskRun.Status.FAILED,
            started_at__gte=since,
            started_at__lt=until,
        )
        .exclude(task_key__in=recovered)
        .order_by("task_key", "-started_at")
    )
    last_failure: dict[str, ScheduledTaskRun] = {}
    for run in failures:
        last_failure.setdefault(run.task_key, run)
    for run in last_failure.values():
        alert_webmasters(run)
    return list(last_failure)


def _prune_run_history(key: str) -> None:
    """Delete this task's run rows older than the retention window — self-pruning history.

    Keeps the append-only ``ScheduledTaskRun`` table bounded without a separate cron job or
    admin surface: every completed run trims its own key's stale rows past
    ``RUN_HISTORY_RETENTION_DAYS``.
    """
    from core.models import ScheduledTaskRun

    cutoff = timezone.now() - timedelta(days=RUN_HISTORY_RETENTION_DAYS)
    ScheduledTaskRun.objects.filter(task_key=key, started_at__lt=cutoff).delete()
