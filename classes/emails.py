"""Outbound class-related emails."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from classes.models import ClassSession, Registration


def _absolute_url(path: str) -> str:
    """Turn a relative path into an absolute URL using the book site base URL."""
    base = getattr(settings, "BOOK_BASE_URL", "https://book.pastlives.space").rstrip("/")
    return f"{base}{path}"


def send_registration_confirmation(registration: "Registration") -> None:
    """Email a registrant their confirmation + self-serve link.

    Sent on payment success (paid classes) or immediately on submit
    (free classes). Idempotent at the call site — the webhook handler
    skips already-confirmed registrations before calling this.
    """
    from classes.models import ClassSettings

    settings_obj = ClassSettings.load()
    offering = registration.class_offering
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    self_serve_path = reverse("classes:my_registration", kwargs={"token": registration.self_serve_token})
    self_serve_url = _absolute_url(self_serve_path)
    context = {
        "registration": registration,
        "offering": offering,
        "upcoming_sessions": upcoming_sessions,
        "self_serve_url": self_serve_url,
        "amount_paid_cents": registration.amount_paid_cents,
        "amount_paid_dollars": f"{registration.amount_paid_cents / 100:.2f}",
        "footer": settings_obj.confirmation_email_footer,
    }
    text_body = render_to_string("classes/emails/confirmation.txt", context)
    html_body = render_to_string("classes/emails/confirmation.html", context)
    subject = f"You're confirmed for {offering.title}"
    send_mail(
        subject=subject,
        message=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[registration.email],
        html_message=html_body,
        fail_silently=False,
    )


def send_instructor_registration_notification(registration: "Registration") -> None:
    """Notify the instructor that someone registered for their class."""
    offering = registration.class_offering
    instructor = offering.instructor
    if not instructor.user.email:
        return
    subject = f"New registration: {registration.first_name} {registration.last_name} for {offering.title}"
    body = (
        f"{registration.first_name} {registration.last_name} ({registration.email}) "
        f'just registered for your class "{offering.title}".\n\n'
        f"Status: {registration.get_status_display()}\n"
        f"Paid: ${registration.amount_paid_cents / 100:.2f}\n\n"
        f"You now have {offering.registrations.count()}/{offering.capacity} spots filled."
    )
    send_mail(
        subject=subject,
        message=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[instructor.user.email],
        fail_silently=True,
    )


def send_admin_registration_notification(registration: "Registration") -> None:
    """Notify admins that someone registered for a class (if configured)."""
    admin_emails = [e.strip() for e in getattr(settings, "CLASS_ADMIN_NOTIFY_EMAILS", "").split(",") if e.strip()]
    if not admin_emails:
        return
    offering = registration.class_offering
    subject = f"[Classes] New registration: {registration.first_name} {registration.last_name} — {offering.title}"
    body = (
        f"{registration.first_name} {registration.last_name} ({registration.email}) "
        f'registered for "{offering.title}" (instructor: {offering.instructor.display_name}).\n\n'
        f"Status: {registration.get_status_display()}\n"
        f"Paid: ${registration.amount_paid_cents / 100:.2f}\n"
        f"Capacity: {offering.registrations.count()}/{offering.capacity}"
    )
    send_mail(
        subject=subject,
        message=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=admin_emails,
        fail_silently=True,
    )


def send_reminder_email(registration: "Registration", session: "ClassSession") -> None:
    """Email a registrant a reminder for an upcoming session."""
    offering = session.class_offering
    self_serve_path = reverse("classes:my_registration", kwargs={"token": registration.self_serve_token})
    self_serve_url = _absolute_url(self_serve_path)
    context = {
        "registration": registration,
        "session": session,
        "offering": offering,
        "self_serve_url": self_serve_url,
    }
    text_body = render_to_string("classes/emails/reminder.txt", context)
    html_body = render_to_string("classes/emails/reminder.html", context)
    subject = f"Reminder: {offering.title} — {session.starts_at:%a %b %-d at %-I:%M %p}"
    send_mail(
        subject=subject,
        message=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[registration.email],
        html_message=html_body,
        fail_silently=False,
    )
