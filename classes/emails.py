"""Outbound class-related emails."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from classes.models import ClassApproval, ClassOffering, ClassSession, Registration


def _admin_review_recipients() -> list[str]:
    """Return the configured admin reviewer email list, deduplicated."""
    raw = getattr(settings, "CLASS_ADMIN_NOTIFY_EMAILS", "") or ""
    seen: list[str] = []
    for chunk in raw.split(","):
        email = chunk.strip()
        if email and email not in seen:
            seen.append(email)
    return seen


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


def send_class_review_requests(offering: "ClassOffering", approvals: list["ClassApproval"]) -> None:
    """Fire the review-request emails created by ``submit_for_review()``.

    Sends one email per approval row:
      * Admin rows go to every address in ``CLASS_ADMIN_NOTIFY_EMAILS``.
      * Guild-lead rows go to the guild_lead's user email when set.

    Each email carries a tokenized ``/classes/review/<token>/`` link so the
    reviewer can act without a hub login. Also sends the instructor a
    "here's what happens next" explainer in the same call.
    """
    from classes.models import ClassApproval

    for row in approvals:
        review_url = _absolute_url(reverse("classes:class_review", kwargs={"token": row.token}))
        recipients: list[str] = []
        if row.role == ClassApproval.Role.ADMIN:
            recipients = _admin_review_recipients()
            role_label = "Admin"
        elif row.role == ClassApproval.Role.GUILD_LEAD:
            guild = offering.category.guild
            lead = guild.guild_lead if guild else None
            if lead and lead.user and lead.user.email:
                recipients = [lead.user.email]
            role_label = "Guild Lead"
        else:
            role_label = row.get_role_display()
        if not recipients:
            continue
        context = {
            "offering": offering,
            "approval": row,
            "review_url": review_url,
            "role_label": role_label,
        }
        text_body = render_to_string("classes/emails/review_request.txt", context)
        html_body = render_to_string("classes/emails/review_request.html", context)
        send_mail(
            subject=f"Review request: {offering.title}",
            message=text_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=recipients,
            html_message=html_body,
            fail_silently=True,
        )

    # Tell the instructor what's happening so they don't wonder.
    if offering.instructor and offering.instructor.user and offering.instructor.user.email:
        instructor_url = _absolute_url(
            reverse("classes:instructor_class_edit", kwargs={"pk": offering.pk})
        )
        ctx = {
            "offering": offering,
            "approvals": approvals,
            "instructor_url": instructor_url,
        }
        text_body = render_to_string("classes/emails/review_submitted_instructor.txt", ctx)
        html_body = render_to_string("classes/emails/review_submitted_instructor.html", ctx)
        send_mail(
            subject=f"Your class “{offering.title}” is in review",
            message=text_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[offering.instructor.user.email],
            html_message=html_body,
            fail_silently=True,
        )


def send_class_review_decision(offering: "ClassOffering", row: "ClassApproval") -> None:
    """Email the instructor when any reviewer records a decision.

    Subject lines vary by outcome so the instructor's inbox tells the story:
      * "Approved" while other gates pending.
      * "Your class is live!" when fully approved.
      * "Changes requested" with reviewer notes verbatim.
      * "Declined" with reviewer notes.
    """
    from classes.models import ClassApproval

    instructor = offering.instructor
    if not (instructor and instructor.user and instructor.user.email):
        return

    fully_approved = (
        offering.status == offering.Status.PUBLISHED
        and row.decision == ClassApproval.Decision.APPROVED
    )
    if fully_approved:
        subject = f"Your class “{offering.title}” is live!"
        public_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        edit_url = public_url
    elif row.decision == ClassApproval.Decision.APPROVED:
        subject = f"{row.get_role_display()} approved “{offering.title}”"
        edit_url = _absolute_url(reverse("classes:instructor_class_edit", kwargs={"pk": offering.pk}))
        public_url = ""
    elif row.decision == ClassApproval.Decision.CHANGES_REQUESTED:
        subject = f"Changes requested on “{offering.title}”"
        edit_url = _absolute_url(reverse("classes:instructor_class_edit", kwargs={"pk": offering.pk}))
        public_url = ""
    else:  # DENIED
        subject = f"Your class submission was declined: “{offering.title}”"
        edit_url = _absolute_url(reverse("classes:instructor_class_edit", kwargs={"pk": offering.pk}))
        public_url = ""

    pending_rows = list(offering.approvals.filter(decision=""))
    context = {
        "offering": offering,
        "approval": row,
        "edit_url": edit_url,
        "public_url": public_url,
        "fully_approved": fully_approved,
        "pending_rows": pending_rows,
    }
    text_body = render_to_string("classes/emails/review_decision.txt", context)
    html_body = render_to_string("classes/emails/review_decision.html", context)
    send_mail(
        subject=subject,
        message=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[instructor.user.email],
        html_message=html_body,
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
