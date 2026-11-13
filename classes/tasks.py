"""Scheduled tasks for the Classes app."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.tasks import task
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.timezone import localtime

from classes.emails import _absolute_url, build_class_reminder_occurrence
from classes.models import (
    MINIMUM_SIGNUPS_WARNING_LEAD,
    ClassOffering,
    ClassSession,
    ClassSettings,
    Registration,
)
from core.events.registry import CLASS_UNDER_MINIMUM
from core.events.scheduler import ScheduledOccurrence, run_due

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


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
    # Live classes only: a class an admin took back to draft (or that was cancelled or
    # archived) keeps its registrations on record, but its page is gone until it is
    # live again, so a reminder would point people at a dead link.
    sessions = ClassSession.objects.filter(
        starts_at__gte=target_start,
        starts_at__lt=target_start + timedelta(hours=1),
        class_offering__status=ClassOffering.Status.PUBLISHED,
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


# --- Minimum signups (#1182) ----------------------------------------------------


def build_minimum_warning_occurrence(offering: ClassOffering) -> ScheduledOccurrence:
    """The warning to a class's instructors that it is under its minimum, a day before the decision.

    ``offering`` carries the :meth:`ClassOfferingQuerySet.with_minimum_signups` annotations.
    The period is the class's own, so each instructor hears once however many ticks see it.
    """
    decided_at: datetime = offering.minimum_decided_at  # type: ignore[attr-defined]  # queryset annotation
    confirmed: int = offering.confirmed_signups  # type: ignore[attr-defined]  # queryset annotation
    minimum = offering.minimum_signups
    if minimum is None:
        raise ValueError(f"Class {offering.pk} has no minimum to warn about.")
    needed = minimum - confirmed
    manage_path = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
    return ScheduledOccurrence(
        event_key=CLASS_UNDER_MINIMUM,
        anchor=decided_at,
        offset=-MINIMUM_SIGNUPS_WARNING_LEAD,
        context={
            "offering": offering,
            "class_title": offering.title,
            "confirmed_count": str(confirmed),
            "minimum_signups": str(minimum),
            "needed_count": str(needed),
            "cancel_at": date_format(localtime(decided_at), r"l, F j \a\t g:i A"),
            "manage_url": _absolute_url(manage_path),
        },
        period=f"offering:{offering.pk}:under-minimum",
        target=offering,
        url=manage_path,
    )


def minimum_warning_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """Yield the under-minimum warnings to consider this tick (a scheduler source, #1182).

    The class reminder's proven band: classes whose registration closes one lead (24 hours)
    from ``now``, within the next hour, and still short of their minimum. The scheduler then
    due-checks each against the tick window and dedupes on the period.
    """
    target = now + MINIMUM_SIGNUPS_WARNING_LEAD
    offerings = ClassOffering.objects.under_minimum().filter(  # type: ignore[misc]  # django-stubs can't see annotate() aliases
        minimum_decided_at__gte=target,
        minimum_decided_at__lt=target + timedelta(hours=1),
    )
    for offering in offerings:
        yield build_minimum_warning_occurrence(offering)


def cancel_classes_under_minimum(now: datetime) -> int:
    """Cancel every class the minimum decides against at ``now``; return how many were cancelled.

    A catch-up sweep (:meth:`ClassOfferingQuerySet.due_minimum_cancel`), so a missed run is
    made up on the next one. Each class is cancelled in its own transaction: one that fails is
    logged and the rest still run, then the failure is raised so the job's run record and the
    Webmaster alert show it.

    Raises:
        RuntimeError: One or more classes could not be cancelled.
    """
    cancelled = 0
    failed: list[int] = []
    for offering in ClassOffering.objects.due_minimum_cancel(now):
        try:
            if offering.cancel_for_minimum(now):
                cancelled += 1
        except Exception:
            logger.exception("Cancelling class %s for its minimum signups failed", offering.pk)
            failed.append(offering.pk)
    if failed:
        raise RuntimeError(f"Could not cancel classes under their minimum: {failed}")
    return cancelled


@task
def refund_cancelled_class_payment(registration_id: int) -> None:
    """Refund one paid signup in full on a cancelled class, with the class's cancellation reason (#1182).

    Queued for every paid site signup when the minimum cancels a class, and for a payment that
    lands after any cancel. Safe to run twice: a signup with nothing left to refund is skipped,
    and so is one with a refund still PENDING at Stripe, which is money already on its way. A
    refund Stripe refuses stays as its FAILED ledger row, and the Billing Administrators get the
    existing refund failed alert for the newest one, with its Retry link, because nobody is on a
    page to see it fail.
    """
    from billing.exceptions import AlreadyRefundedError, RefundError, RefundNotPossibleError
    from billing.models import PaymentRefund
    from billing.refunds import alert_refund_failed

    registration = Registration.objects.select_related("class_offering").get(pk=registration_id)
    if not registration.refund_payment_intent_id or registration.refundable_cents <= 0:
        return
    if registration.refunds.filter(status=PaymentRefund.Status.PENDING).exists():
        return
    try:
        registration.issue_refund(reason=registration.class_offering.cancellation_reason)
    except (AlreadyRefundedError, RefundNotPossibleError):
        return
    except RefundError:
        failed = registration.refunds.filter(status=PaymentRefund.Status.FAILED).order_by("-created_at", "-pk").first()
        if failed is None:
            raise
        alert_refund_failed(failed)


def enforce_class_minimums(window_minutes: int = 15) -> tuple[int, int]:
    """The job: send this tick's under-minimum warnings, then cancel the classes that closed short.

    Returns:
        ``(warned, cancelled)``: warnings delivered this tick and classes cancelled.
    """
    now = timezone.now()
    warned = run_due(minimum_warning_occurrences(now), now=now, window=timedelta(minutes=window_minutes))
    cancelled = cancel_classes_under_minimum(now)
    return warned, cancelled
