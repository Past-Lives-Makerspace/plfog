"""Scheduled tasks for the Classes app."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

from classes.emails import build_class_reminder_occurrence
from classes.models import ClassSession, ClassSettings, Registration
from core.events.scheduler import run_due

if TYPE_CHECKING:
    from collections.abc import Iterable

    from core.events.scheduler import ScheduledOccurrence


def class_reminder_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """Yield the class-reminder occurrences to consider this tick (a scheduler source).

    Windows the query the proven way: select sessions starting in the band that
    makes ``starts_at − reminder_hours_before`` land near ``now`` (the scheduler
    then due-checks each against the 15-minute tick window), and pair each with its
    CONFIRMED registrations. The scheduler fires the due ones via ``emit`` and
    dedupes on :class:`core.models.EventDelivery` — so there is no longer a separate
    ``RegistrationReminder`` guard (the per-(registration, session) ``period`` bucket
    is the single dedupe authority now, design §2.5/§2.6).
    """
    hours_before = ClassSettings.load().reminder_hours_before or 24
    # Prefilter sessions to a generous band around the fire time; the scheduler's
    # is_due() applies the exact half-open tick window. The band is [fire, fire+1h)
    # in anchor space, comfortably covering any reasonable tick width.
    target_start = now + timedelta(hours=hours_before)
    sessions = ClassSession.objects.filter(
        starts_at__gte=target_start,
        starts_at__lt=target_start + timedelta(hours=1),
    ).select_related("class_offering")
    for session in sessions:
        registrations = Registration.objects.filter(
            class_offering=session.class_offering,
            status=Registration.Status.CONFIRMED,
        )
        for registration in registrations:
            yield build_class_reminder_occurrence(registration, session, hours_before=hours_before)


def send_due_class_reminders(window_minutes: int = 15) -> int:
    """Fire reminders for sessions whose ``starts_at − reminder_hours_before`` is due.

    Now a thin driver over the generalized scheduler: it builds this tick's
    occurrences (:func:`class_reminder_occurrences`) and hands them to
    :func:`core.events.scheduler.run_due`, which due-checks each against the
    ``window_minutes`` tick window and fires the due ones through ``emit`` (deduped
    on :class:`core.models.EventDelivery`).

    Returns the number of reminders actually delivered (a re-run within the same
    window returns 0 — the EventDelivery rows dedupe it), preserving the old return
    contract.
    """
    now = timezone.now()
    return run_due(
        class_reminder_occurrences(now),
        now=now,
        window=timedelta(minutes=window_minutes),
    )
