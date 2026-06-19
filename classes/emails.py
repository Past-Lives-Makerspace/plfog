"""Outbound class-related emails."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.template.loader import render_to_string

from core import email as core_email
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
    core_email.send(
        to=registration.email,
        subject=subject,
        trigger_kind="classes.registration_confirmation",
        text_body=text_body,
        html_body=html_body,
    )


def send_instructor_registration_notification(registration: "Registration") -> None:
    """Notify the instructor that someone registered for their class."""
    offering = registration.class_offering
    instructor = offering.instructor
    if not instructor or not instructor.primary_email:
        return
    subject = f"New registration: {registration.first_name} {registration.last_name} for {offering.title}"
    body = (
        f"{registration.first_name} {registration.last_name} ({registration.email}) "
        f'just registered for your class "{offering.title}".\n\n'
        f"Status: {registration.get_status_display()}\n"
        f"Paid: ${registration.amount_paid_cents / 100:.2f}\n\n"
        f"You now have {offering.registrations.count()}/{offering.capacity} spots filled."
    )
    core_email.send(
        to=instructor.primary_email,
        subject=subject,
        trigger_kind="classes.instructor_registration",
        text_body=body,
        best_effort=True,
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
        f'registered for "{offering.title}" (instructor: {offering.instructor.display_name if offering.instructor else "N/A"}).\n\n'
        f"Status: {registration.get_status_display()}\n"
        f"Paid: ${registration.amount_paid_cents / 100:.2f}\n"
        f"Capacity: {offering.registrations.count()}/{offering.capacity}"
    )
    core_email.send(
        to=admin_emails,
        subject=subject,
        trigger_kind="classes.admin_registration",
        text_body=body,
        best_effort=True,
    )


def _send_review_request_email(
    offering: "ClassOffering", row: "ClassApproval", *, recipients: list[str], role_label: str
) -> None:
    """Render and send the stage-one ``review_request`` email to ``recipients``.

    Each email carries a tokenized ``/classes/review/<token>/`` link so the
    reviewer can act without a hub login. No-op when there are no recipients.
    """
    if not recipients:
        return
    review_url = _absolute_url(reverse("classes:class_review", kwargs={"token": row.token}))
    context = {
        "offering": offering,
        "approval": row,
        "review_url": review_url,
        "role_label": role_label,
    }
    text_body = render_to_string("classes/emails/review_request.txt", context)
    html_body = render_to_string("classes/emails/review_request.html", context)
    core_email.send(
        to=recipients,
        subject=f"Review request: {offering.title}",
        trigger_kind="classes.review_request",
        text_body=text_body,
        html_body=html_body,
        best_effort=True,
    )


def _send_instructor_review_explainer(offering: "ClassOffering", row: "ClassApproval") -> None:
    """Tell the instructor their class is in review so they don't wonder."""
    if not (offering.instructor and offering.instructor.primary_email):
        return
    instructor_url = _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
    ctx = {
        "offering": offering,
        "approvals": [row],
        "instructor_url": instructor_url,
    }
    text_body = render_to_string("classes/emails/review_submitted_instructor.txt", ctx)
    html_body = render_to_string("classes/emails/review_submitted_instructor.html", ctx)
    core_email.send(
        to=offering.instructor.primary_email,
        subject=f"Your class '{offering.title}' is in review",
        trigger_kind="classes.review_request_instructor",
        text_body=text_body,
        html_body=html_body,
        best_effort=True,
    )


def send_guild_lead_review_request(offering: "ClassOffering", approval: "ClassApproval") -> None:
    """Stage one: email the guild lead the review request + tell the instructor.

    Fired from ``ClassOffering.submit_for_review()`` when the first-stage gate
    is the Guild Lead. The guild lead's address resolves from the category's
    guild; when it's missing, only the instructor explainer goes out.
    """
    guild = offering.category.guild if offering.category_id else None
    lead = guild.guild_lead if guild else None
    recipients = [lead.primary_email] if (lead and lead.primary_email) else []
    _send_review_request_email(offering, approval, recipients=recipients, role_label="Guild Lead")
    _send_instructor_review_explainer(offering, approval)


def send_admin_review_request(offering: "ClassOffering", approval: "ClassApproval") -> None:
    """Stage one for lead-less categories: email admins the review request.

    Used when a category has no guild lead, so the Admin gate is stage one.
    Mirrors the guild-lead path: admins get the request, the instructor gets
    the explainer.
    """
    _send_review_request_email(offering, approval, recipients=_admin_review_recipients(), role_label="Admin")
    _send_instructor_review_explainer(offering, approval)


def send_admin_validation_request(offering: "ClassOffering", approval: "ClassApproval") -> None:
    """Stage two: email admins for executive validation after a guild-lead approval.

    Fired from ``ClassOffering.on_review_decision_recorded`` when a Guild Lead
    approves and the Admin gate opens. Carries the admin row's tokenized review
    link and the "executive validation" wording from the spec.
    """
    recipients = _admin_review_recipients()
    if not recipients:
        return
    review_url = _absolute_url(reverse("classes:class_review", kwargs={"token": approval.token}))
    guild = offering.category.guild if offering.category_id else None
    lead = guild.guild_lead if guild else None
    context = {
        "offering": offering,
        "approval": approval,
        "review_url": review_url,
        "guild_lead_name": lead.display_name if lead is not None else "A guild lead",
        "instructor_name": offering.instructor.display_name if offering.instructor is not None else "the instructor",
    }
    text_body = render_to_string("classes/emails/admin_validation_request.txt", context)
    html_body = render_to_string("classes/emails/admin_validation_request.html", context)
    core_email.send(
        to=recipients,
        subject=f"Executive validation needed: {offering.title}",
        trigger_kind="classes.admin_validation_request",
        text_body=text_body,
        html_body=html_body,
        best_effort=True,
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
    if not (instructor and instructor.primary_email):
        return

    fully_approved = offering.status == offering.Status.PUBLISHED and row.decision == ClassApproval.Decision.APPROVED
    if fully_approved:
        subject = f"Your class '{offering.title}' is live!"
        public_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        edit_url = public_url
    elif row.decision == ClassApproval.Decision.APPROVED:
        subject = f"{row.get_role_display()} approved '{offering.title}'"
        edit_url = _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        public_url = ""
    elif row.decision == ClassApproval.Decision.CHANGES_REQUESTED:
        subject = f"Changes requested on '{offering.title}'"
        edit_url = _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        public_url = ""
    else:  # DENIED
        subject = f"Your class submission was declined: '{offering.title}'"
        edit_url = _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
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
    core_email.send(
        to=instructor.primary_email,
        subject=subject,
        trigger_kind="classes.review_decision",
        text_body=text_body,
        html_body=html_body,
        best_effort=True,
    )


def send_waitlist_joined_confirmation(registration: "Registration") -> None:
    """Confirm to a registrant that they're on the waitlist + their position.

    Fires on the WAITLISTED-creating branch of the register view (sold-out
    class + waitlist intent). Tells them what position they're in and what
    happens if a spot opens.
    """
    offering = registration.class_offering
    self_serve_url = _absolute_url(reverse("classes:my_registration", kwargs={"token": registration.self_serve_token}))
    ctx = {
        "registration": registration,
        "offering": offering,
        "position": registration.waitlist_position,
        "self_serve_url": self_serve_url,
    }
    text_body = render_to_string("classes/emails/waitlist_joined.txt", ctx)
    html_body = render_to_string("classes/emails/waitlist_joined.html", ctx)
    core_email.send(
        to=registration.email,
        subject=f"You're on the waitlist for {offering.title}",
        trigger_kind="classes.waitlist_joined",
        text_body=text_body,
        html_body=html_body,
    )


def send_waitlist_spot_opened(registration: "Registration") -> None:
    """Notify a waitlisted registrant that a spot just opened.

    Fires from ``ClassOffering.promote_next_from_waitlist`` after a confirmed
    registration cancels or refunds. The link lands on the registration page
    where the registrant can complete payment within the claim window
    configured on ClassSettings.
    """
    from classes.models import ClassSettings

    offering = registration.class_offering
    register_url = _absolute_url(
        reverse("classes:register", kwargs={"slug": offering.slug}) + f"?waitlist_token={registration.self_serve_token}"
    )
    settings_obj = ClassSettings.load()
    ctx = {
        "registration": registration,
        "offering": offering,
        "register_url": register_url,
        "claim_window_hours": settings_obj.waitlist_claim_window_hours,
    }
    text_body = render_to_string("classes/emails/waitlist_spot_opened.txt", ctx)
    html_body = render_to_string("classes/emails/waitlist_spot_opened.html", ctx)
    core_email.send(
        to=registration.email,
        subject=f"A spot opened in {offering.title}!",
        trigger_kind="classes.waitlist_spot_opened",
        text_body=text_body,
        html_body=html_body,
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
    core_email.send(
        to=registration.email,
        subject=subject,
        trigger_kind="classes.reminder",
        text_body=text_body,
        html_body=html_body,
    )
