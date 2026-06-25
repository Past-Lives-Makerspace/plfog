"""Scheduled tasks for the Classes app."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from classes.emails import send_reminder_email
from classes.models import ClassSession, ClassSettings, Registration, RegistrationReminder


def send_due_class_reminders(window_minutes: int = 15) -> int:
    """Email confirmed registrants about sessions starting in ``reminder_hours_before``.

    Finds sessions whose ``starts_at - reminder_hours_before`` falls in the
    last ``window_minutes``. Records a ``RegistrationReminder`` row per
    (registration, session) so the same reminder never fires twice.

    Returns the number of reminders sent.
    """
    settings_obj = ClassSettings.load()
    hours_before = settings_obj.reminder_hours_before or 24
    now = timezone.now()
    # Sessions starting within [now + hours_before, now + hours_before + window)
    # — i.e. it's been at most `window_minutes` since we crossed the send threshold.
    target_start = now + timedelta(hours=hours_before)
    target_end = target_start + timedelta(minutes=window_minutes)

    sessions = ClassSession.objects.filter(
        starts_at__gte=target_start,
        starts_at__lt=target_end,
    ).select_related("class_offering")

    sent = 0
    for session in sessions:
        registrations = Registration.objects.filter(
            class_offering=session.class_offering,
            status=Registration.Status.CONFIRMED,
        )
        for registration in registrations:
            _, created = RegistrationReminder.objects.get_or_create(registration=registration, session=session)
            if not created:
                continue
            # ``send_reminder_email`` emits the single ``class_reminder`` event that fans out
            # BOTH the dedicated reminder email (to the registrant) and the in-app row (to the
            # member's linked user) — replacing the old dedicated send + suppressed ``dispatch``.
            send_reminder_email(registration, session)
            sent += 1
    return sent
