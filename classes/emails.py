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
    from core.events.scheduler import ScheduledOccurrence
    from membership.models import Guild, Member


def _admin_recipients() -> list[str]:
    """Email addresses for admin notifications, deduplicated and order-preserving.

    Resolves every Member with the Admin role (via their primary email), then
    unions in any addresses configured via ``CLASS_ADMIN_NOTIFY_EMAILS``. The
    setting is an optional extra — admins on the roster are notified out of the
    box, with no per-environment configuration required.
    """
    from membership.models import Member

    seen: list[str] = []
    for member in Member.objects.filter(fog_role=Member.FogRole.ADMIN):
        email = member.primary_email
        if email and email not in seen:
            seen.append(email)
    raw = getattr(settings, "CLASS_ADMIN_NOTIFY_EMAILS", "") or ""
    for chunk in raw.split(","):
        email = chunk.strip()
        if email and email not in seen:
            seen.append(email)
    return seen


def _guild_leadership_recipients(guild: "Guild | None") -> list[str]:
    """Every distinct email for the guild's lead and staff — they share review duties.

    Staff (co-leads, secretaries, treasurers, orienters) carry full guild-lead
    permissions, so each guild-lead review request fans out to all of them.
    """
    if guild is None:
        return []
    seen: list[str] = []
    for member in guild.leadership_members():
        email = member.primary_email
        if email and email not in seen:
            seen.append(email)
    return seen


def _absolute_url(path: str) -> str:
    """Turn a relative path into an absolute URL using the book site base URL."""
    base = getattr(settings, "BOOK_BASE_URL", "https://book.pastlives.space").rstrip("/")
    return f"{base}{path}"


def send_registration_confirmation(registration: "Registration") -> None:
    """Emit a registrant's confirmation: the rich confirmation email + one in-app row.

    Sent on payment success (paid classes) or immediately on submit (free classes).
    Idempotent at the call site — the webhook handler skips already-confirmed
    registrations before calling this.

    One ``registration_confirmed`` event replaces the old dedicated send + the model's
    suppressed in-app ``dispatch``: the preserved confirmation shell (sessions,
    self-serve link, footer) goes to ``registration.email`` via ``email_to`` (raw,
    guest/alias-safe), while the ``registrant`` resolver posts the in-app "Registration
    confirmed" row to the member's linked user — exactly the recipients as before, now
    via a single path so there is never a second email or a duplicate bell row.
    """
    from classes.models import ClassSettings

    settings_obj = ClassSettings.load()
    offering = registration.class_offering
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    self_serve_path = reverse("classes:my_registration", kwargs={"token": registration.self_serve_token})
    self_serve_url = _absolute_url(self_serve_path)
    template_context = {
        "registration": registration,
        "offering": offering,
        "upcoming_sessions": upcoming_sessions,
        "self_serve_url": self_serve_url,
        "amount_paid_cents": registration.amount_paid_cents,
        "amount_paid_dollars": f"{registration.amount_paid_cents / 100:.2f}",
        "footer": settings_obj.confirmation_email_footer,
    }

    from core.events.senders import emit_with_email_shell

    emit_with_email_shell(
        "registration_confirmed",
        actor=registration.member.user if registration.member is not None else None,
        target=registration,
        context={"member": registration.member},
        subject=f"You're confirmed for {offering.title}",
        text_template="classes/emails/confirmation.txt",
        html_template="classes/emails/confirmation.html",
        template_context=template_context,
        in_app_title="Registration confirmed",
        in_app_body=offering.title,
        url="/classes/account/",
        email_to=registration.email,
        period=f"reg:{registration.pk}:confirmation",
    )


def _welcome_email_bodies(offering: "ClassOffering", *, greeting_name: str, self_serve_url: str) -> tuple[str, str]:
    """Render the (text, html) bodies for an instructor's welcome email."""
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    context = {
        "offering": offering,
        "greeting_name": greeting_name,
        "upcoming_sessions": upcoming_sessions,
        "self_serve_url": self_serve_url,
        "body": offering.welcome_email_body,
    }
    text_body = render_to_string("classes/emails/welcome.txt", context)
    html_body = render_to_string("classes/emails/welcome.html", context)
    return text_body, html_body


def send_class_welcome_email(registration: "Registration") -> None:
    """Send the instructor-authored welcome email to a newly-confirmed registrant.

    Best-effort and a no-op unless the class's welcome email is enabled and has a
    subject + body (``welcome_email_ready``). Called right after the order
    confirmation on the free-class and paid-webhook confirmation paths, so it
    reaches only people who are actually in the class (never waitlist joins).
    """
    offering = registration.class_offering
    if not offering.welcome_email_ready:
        return
    self_serve_url = _absolute_url(reverse("classes:my_registration", kwargs={"token": registration.self_serve_token}))
    text_body, html_body = _welcome_email_bodies(
        offering, greeting_name=registration.first_name, self_serve_url=self_serve_url
    )
    core_email.send(
        to=registration.email,
        subject=offering.welcome_email_subject,
        trigger_kind="classes.welcome_email",
        text_body=text_body,
        html_body=html_body,
        best_effort=True,
    )


def send_class_welcome_email_test(offering: "ClassOffering", recipient_email: str) -> None:
    """Send a test render of a class's welcome email to one address (the editor)."""
    text_body, html_body = _welcome_email_bodies(
        offering, greeting_name="there", self_serve_url=_absolute_url(reverse("classes:public_list"))
    )
    core_email.send(
        to=recipient_email,
        subject=f"[Test] {offering.welcome_email_subject}",
        trigger_kind="classes.welcome_email_test",
        text_body=text_body,
        html_body=html_body,
        best_effort=True,
    )


def emit_instructor_new_registration(registration: "Registration") -> None:
    """Emit the instructor's new-registration notice: flat email + in-app row, one event.

    The flat email body is preserved exactly and addressed to
    ``instructor.primary_email`` (which may be an alias differing from the login
    email); the in-app row goes to the instructor the ``instructor`` resolver
    finds. No-op when the offering has no instructor.
    """
    from core.events.channels import Message
    from core.events.emit import emit
    from core.events.registry import Channel

    offering = registration.class_offering
    instructor = offering.instructor
    if instructor is None:
        return
    subject = f"New registration: {registration.first_name} {registration.last_name} for {offering.title}"
    body = (
        f"{registration.first_name} {registration.last_name} ({registration.email}) "
        f'just registered for your class "{offering.title}".\n\n'
        f"Status: {registration.get_status_display()}\n"
        f"Paid: ${registration.amount_paid_cents / 100:.2f}\n\n"
        f"You now have {offering.registrations.count()}/{offering.capacity} spots filled."
    )
    # No trigger_kind → emit labels the audit row with the event key
    # (instructor_new_registration), one vocabulary so the log joins to the event + prefs.
    email_message = Message(
        title=subject,
        body=body,
        url="/classes/teach/",
    )
    emit(
        "instructor_new_registration",
        context={"offering": offering},
        title="New registration",
        body=offering.title,
        url="/classes/teach/",
        messages={Channel.EMAIL: email_message},
        email_to=instructor.primary_email or None,
        period=f"reg:{registration.pk}:instructor_notice",
    )


def send_admin_registration_notification(registration: "Registration") -> None:
    """Emit the admins' new-registration CC: one flat email, no in-app row.

    Mirrors the instructor sibling (:func:`emit_instructor_new_registration`) on the
    spine: the flat admin-CC body is preserved exactly and addressed to the
    ``_admin_recipients()`` set via ``email_to`` (byte-identical recipients to today).
    The event resolves the in-app audience from an empty ``instructor`` context so the
    resolver finds nobody — admins get the email only, never a bell row (they never had
    one). The event logs no activity, and its distinct ``period`` + admin ``email_to``
    keep it independent of the instructor's own ``instructor_new_registration`` emit.
    """
    admin_emails = _admin_recipients()
    if not admin_emails:
        return

    from core.events.channels import Message
    from core.events.emit import emit
    from core.events.registry import Channel

    offering = registration.class_offering
    subject = f"[Classes] New registration: {registration.first_name} {registration.last_name} — {offering.title}"
    body = (
        f"{registration.first_name} {registration.last_name} ({registration.email}) "
        f'registered for "{offering.title}" (instructor: {offering.instructor.display_name if offering.instructor else "N/A"}).\n\n'
        f"Status: {registration.get_status_display()}\n"
        f"Paid: ${registration.amount_paid_cents / 100:.2f}\n"
        f"Capacity: {offering.registrations.count()}/{offering.capacity}"
    )
    # No trigger_kind → emit labels the audit row with the event key (one vocabulary).
    email_message = Message(title=subject, body=body)
    emit(
        "instructor_new_registration",
        target=registration,
        context={"instructor": None},
        messages={Channel.EMAIL: email_message},
        email_to=admin_emails,
        period=f"reg:{registration.pk}:admin_notice",
    )


def _emit_review_request(
    offering: "ClassOffering",
    row: "ClassApproval",
    *,
    recipients: list[str],
    role_label: str,
    guild: "Guild | None",
    instructor_name: str,
) -> None:
    """Emit the stage-one ``class_review_requested`` event: review email + in-app row.

    The reviewer email is the preserved ``review_request.{txt,html}`` shell (tokenized
    ``/classes/review/<token>/`` link), addressed to the exact ``recipients`` list via
    ``email_to`` (lead+staff for the guild-lead gate, admins for the lead-less gate). The
    in-app "A class needs your review" row resolves from the event's ``GUILD_LEADERSHIP``
    resolver against ``guild`` — so the guild's whole leadership gets a bell row, and a
    ``None`` guild (lead-less category routed to admins) yields no bell row, exactly as
    today (the admin branch was email-only). No-op on the email when there are no
    recipients; the in-app still fans out to any resolvable leadership.
    """
    from core.events.senders import emit_with_email_shell

    review_url = _absolute_url(reverse("classes:class_review", kwargs={"token": row.token}))
    guild_name = guild.name if guild is not None else ""
    template_context = {
        "offering": offering,
        "approval": row,
        "review_url": review_url,
        "role_label": role_label,
    }
    emit_with_email_shell(
        "class_review_requested",
        target=offering,
        context={"guild": guild},
        subject=f"Review request: {offering.title}",
        text_template="classes/emails/review_request.txt",
        html_template="classes/emails/review_request.html",
        template_context=template_context,
        in_app_title="A class needs your review",
        in_app_body=(
            f"{instructor_name} in the {guild_name} Guild requests approval for their upcoming class dates."
            if guild is not None
            else f"{instructor_name} requests approval for their upcoming class dates."
        ),
        url="/classes/teach/",
        email_to=recipients or None,
        period=f"approval:{row.pk}:request",
    )


def _emit_instructor_review_explainer(offering: "ClassOffering", row: "ClassApproval") -> None:
    """Email the instructor that their class is in review (email-only, no bell row).

    Routes the preserved ``review_submitted_instructor.{txt,html}`` shell through the
    spine as the ``class_review_requested`` EMAIL channel, addressed to the instructor's
    ``primary_email`` via ``email_to``. The resolver is given a ``None`` guild so no in-app
    row is created (the instructor never had one) — the explainer's audit label
    (``classes.review_request_instructor``) is preserved, and its ``email:<instructor>``
    dedup ref keeps it independent of the reviewer email. No-op without an instructor email.
    """
    if not (offering.instructor and offering.instructor.primary_email):
        return
    from core.events.senders import emit_with_email_shell

    instructor_url = _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
    template_context = {
        "offering": offering,
        "approvals": [row],
        "instructor_url": instructor_url,
    }
    emit_with_email_shell(
        "class_review_requested",
        target=offering,
        context={"guild": None},
        subject=f"Your class '{offering.title}' is in review",
        text_template="classes/emails/review_submitted_instructor.txt",
        html_template="classes/emails/review_submitted_instructor.html",
        template_context=template_context,
        url=instructor_url,
        email_to=offering.instructor.primary_email,
        period=f"approval:{row.pk}:instructor_explainer",
    )


def send_guild_lead_review_request(offering: "ClassOffering", approval: "ClassApproval") -> None:
    """Stage one: review request to the guild's lead and staff + the instructor explainer.

    Fired from ``ClassOffering._notify_first_stage_reviewer`` when the first-stage gate is
    the Guild Lead. One ``class_review_requested`` event sends the dedicated review email
    to the category's guild leadership (lead plus every staff member — they share review
    duties) AND posts the in-app row to that same leadership; a second event sends the
    instructor explainer (email only). This collapses the old model ``dispatch`` +
    dedicated send into a single path, so opted-in leadership get exactly one email and
    one bell row. When no leadership has an email, only the instructor explainer goes out.
    """
    guild = offering.category.guild if offering.category_id else None
    recipients = _guild_leadership_recipients(guild)
    instructor_name = offering.instructor.display_name if offering.instructor is not None else "An instructor"
    _emit_review_request(
        offering, approval, recipients=recipients, role_label="Guild Lead", guild=guild, instructor_name=instructor_name
    )
    _emit_instructor_review_explainer(offering, approval)


def send_admin_review_request(offering: "ClassOffering", approval: "ClassApproval") -> None:
    """Stage one for lead-less categories: email admins the review request.

    Used when a category has no guild lead, so the Admin gate is stage one. The review
    email goes to the ``_admin_recipients()`` set (byte-identical to today) with no in-app
    row (the admin branch was email-only — the ``None`` guild yields no leadership bell
    row); the instructor still gets the explainer.
    """
    instructor_name = offering.instructor.display_name if offering.instructor is not None else "An instructor"
    _emit_review_request(
        offering,
        approval,
        recipients=_admin_recipients(),
        role_label="Admin",
        guild=None,
        instructor_name=instructor_name,
    )
    _emit_instructor_review_explainer(offering, approval)


def send_admin_validation_request(offering: "ClassOffering", approval: "ClassApproval") -> None:
    """Stage two: emit the executive-validation request after a guild-lead approval.

    Fired from ``ClassOffering._escalate_to_admin`` when a Guild Lead approves and the
    Admin gate opens. One ``class_validation_requested`` event: the structural
    ``admin_validation_request.{txt,html}`` shell is preserved as the email (to the
    same admin recipients as before — ``_admin_recipients()``), and the in-app row
    resolves to the FOG_ADMINS audience. ``class_validation_requested`` logs no
    SiteActivity, so the emit introduces no activity-row duplication.
    """
    from core.events.senders import emit_with_email_shell

    recipients = _admin_recipients()
    review_url = _absolute_url(reverse("classes:class_review", kwargs={"token": approval.token}))
    guild = offering.category.guild if offering.category_id else None
    lead = guild.guild_lead if guild else None
    template_context = {
        "offering": offering,
        "approval": approval,
        "review_url": review_url,
        "guild_lead_name": lead.display_name if lead is not None else "A guild lead",
        "instructor_name": offering.instructor.display_name if offering.instructor is not None else "the instructor",
    }
    instructor_name = template_context["instructor_name"]
    lead_name = template_context["guild_lead_name"]
    emit_with_email_shell(
        "class_validation_requested",
        target=offering,
        context={},
        subject=f"Executive validation needed: {offering.title}",
        text_template="classes/emails/admin_validation_request.txt",
        html_template="classes/emails/admin_validation_request.html",
        template_context=template_context,
        in_app_title="A class needs executive validation",
        in_app_body=f"{lead_name} and {instructor_name} request executive validation to publish this class.",
        url="/classes/admin/",
        email_to=recipients,
        period=f"approval:{approval.pk}:validation",
    )


def send_class_review_decision(offering: "ClassOffering", row: "ClassApproval") -> None:
    """Emit the instructor's review-decision notice when any reviewer decides.

    One event per call carries the rich, outcome-specific decision email (preserved
    ``review_decision.{txt,html}`` shell) to the instructor via ``email_to`` plus — on
    the two outcomes that pinged the instructor's bell before — the matching in-app row.
    This collapses the old dedicated email + the model's ``instructor_class_approved`` /
    ``instructor_changes_requested`` ``dispatch`` into a single path, so an opted-in
    instructor gets exactly one email and one bell row, never two emails.

    Subject lines vary by outcome so the instructor's inbox tells the story:
      * "Approved" while other gates pending (email only — no bell row before).
      * "Your class is live!" when fully approved (email + ``instructor_class_approved`` bell).
      * "Changes requested" with reviewer notes verbatim (email + ``instructor_changes_requested`` bell).
      * "Declined" with reviewer notes (email only — no bell row before).
    """
    from classes.models import ClassApproval
    from core.events.senders import emit_with_email_shell

    instructor = offering.instructor
    if not (instructor and instructor.primary_email):
        return

    fully_approved = offering.status == offering.Status.PUBLISHED and row.decision == ClassApproval.Decision.APPROVED
    # event_key drives the instructor's bell row; (in_app_title, in_app_body) empty means
    # email-only (the resolver still finds the instructor, but emit creates no blank row).
    # ``resolver_instructor`` is the instructor only when a bell row is wanted, else None
    # so the resolver finds nobody and no in-app row is created.
    if fully_approved:
        subject = f"Your class '{offering.title}' is live!"
        public_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        edit_url = public_url
        event_key = "instructor_class_approved"
        in_app_title, in_app_body, in_app_url = "Your class was approved", offering.title, f"/classes/{offering.slug}/"
        resolver_instructor: "Member | None" = instructor
    elif row.decision == ClassApproval.Decision.APPROVED:
        subject = f"{row.get_role_display()} approved '{offering.title}'"
        edit_url = _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        public_url = ""
        event_key = "instructor_class_approved"
        in_app_title, in_app_body, in_app_url = "", "", edit_url
        resolver_instructor = None
    elif row.decision == ClassApproval.Decision.CHANGES_REQUESTED:
        subject = f"Changes requested on '{offering.title}'"
        edit_url = _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        public_url = ""
        event_key = "instructor_changes_requested"
        in_app_title, in_app_body, in_app_url = (
            "Changes requested on your class",
            offering.title,
            f"/classes/{offering.slug}/",
        )
        resolver_instructor = instructor
    else:  # DENIED
        subject = f"Your class submission was declined: '{offering.title}'"
        edit_url = _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        public_url = ""
        event_key = "instructor_changes_requested"
        in_app_title, in_app_body, in_app_url = "", "", edit_url
        resolver_instructor = None

    pending_rows = list(offering.approvals.filter(decision=""))
    template_context = {
        "offering": offering,
        "approval": row,
        "edit_url": edit_url,
        "public_url": public_url,
        "fully_approved": fully_approved,
        "pending_rows": pending_rows,
    }
    emit_with_email_shell(
        event_key,
        target=offering,
        context={"instructor": resolver_instructor},
        subject=subject,
        text_template="classes/emails/review_decision.txt",
        html_template="classes/emails/review_decision.html",
        template_context=template_context,
        in_app_title=in_app_title,
        in_app_body=in_app_body,
        url=in_app_url,
        email_to=instructor.primary_email,
        period=f"approval:{row.pk}:decision",
    )


def send_waitlist_joined_confirmation(registration: "Registration") -> None:
    """Confirm to a registrant that they're on the waitlist + their position.

    Fires on the WAITLISTED-creating branch of the register view (sold-out
    class + waitlist intent). Tells them what position they're in and what
    happens if a spot opens.
    """
    from core.events.senders import emit_with_email_shell

    offering = registration.class_offering
    self_serve_url = _absolute_url(reverse("classes:my_registration", kwargs={"token": registration.self_serve_token}))
    template_context = {
        "registration": registration,
        "offering": offering,
        "position": registration.waitlist_position,
        "self_serve_url": self_serve_url,
    }
    emit_with_email_shell(
        "waitlist_confirmed",
        actor=registration.member.user if registration.member is not None else None,
        target=registration,
        context={"member": registration.member},
        subject=f"You're on the waitlist for {offering.title}",
        text_template="classes/emails/waitlist_joined.txt",
        html_template="classes/emails/waitlist_joined.html",
        template_context=template_context,
        in_app_title="Added to the waitlist",
        in_app_body=offering.title,
        url="/classes/account/",
        email_to=registration.email,
        period=f"reg:{registration.pk}:waitlist_joined",
    )


def send_waitlist_spot_opened(registration: "Registration") -> None:
    """Emit the waitlist-spot-opened notice: claim email + one in-app row.

    Fires from ``ClassOffering.promote_next_from_waitlist`` after a confirmed
    registration cancels or refunds. One ``waitlist_spot_available`` event replaces the
    old dedicated send + the model's in-app ``dispatch``: the preserved claim email
    (lands on the registration page, claim-window link) goes to ``registration.email``
    via ``email_to``, while the ``next_waitlisted`` resolver posts the in-app row to the
    promoted member — same recipients, one path, no second email or duplicate bell row.
    """
    from classes.models import ClassSettings
    from core.events.senders import emit_with_email_shell

    offering = registration.class_offering
    register_url = _absolute_url(
        reverse("classes:register", kwargs={"slug": offering.slug}) + f"?waitlist_token={registration.self_serve_token}"
    )
    settings_obj = ClassSettings.load()
    template_context = {
        "registration": registration,
        "offering": offering,
        "register_url": register_url,
        "claim_window_hours": settings_obj.waitlist_claim_window_hours,
    }
    emit_with_email_shell(
        "waitlist_spot_available",
        actor=registration.member.user if registration.member is not None else None,
        target=registration,
        context={"member": registration.member},
        subject=f"A spot opened in {offering.title}!",
        text_template="classes/emails/waitlist_spot_opened.txt",
        html_template="classes/emails/waitlist_spot_opened.html",
        template_context=template_context,
        in_app_title="A waitlist spot opened up",
        in_app_body=offering.title,
        url="/classes/account/",
        email_to=registration.email,
        period=f"reg:{registration.pk}:waitlist_spot_opened",
    )


def build_class_reminder_occurrence(
    registration: "Registration",
    session: "ClassSession",
    *,
    hours_before: int,
) -> "ScheduledOccurrence":
    """Build the scheduled ``class_reminder`` occurrence for one (registration, session).

    The generalized scheduler (:mod:`core.events.scheduler`) walks these: the
    occurrence's timing is ``session.starts_at − hours_before`` and its dedupe
    ``period`` is ``reg:<pk>:reminder:<session_pk>`` — the SAME bucket the immediate
    send used, so the class reminder fires exactly once per (registration, session)
    via :class:`core.models.EventDelivery` alone (no separate
    ``RegistrationReminder`` row needed — design §2.5/§2.6).

    The reminder EMAIL stays a rich structural shell (``reminder.{txt,html}``),
    rendered into a per-channel ``Channel.EMAIL`` message so the body is byte-for-byte
    identical to today; the in-app row + push render from copy (``title``/``body``).
    """
    from datetime import timedelta

    from core.events.channels import Message
    from core.events.registry import Channel
    from core.events.scheduler import ScheduledOccurrence
    from core.events.senders import email_shell_message

    offering = session.class_offering
    self_serve_path = reverse("classes:my_registration", kwargs={"token": registration.self_serve_token})
    self_serve_url = _absolute_url(self_serve_path)
    template_context = {
        "registration": registration,
        "session": session,
        "offering": offering,
        "self_serve_url": self_serve_url,
    }
    email_message: Message = email_shell_message(
        event_key="class_reminder",
        subject=f"Reminder: {offering.title} — {session.starts_at:%a %b %-d at %-I:%M %p}",
        text_template="classes/emails/reminder.txt",
        html_template="classes/emails/reminder.html",
        template_context=template_context,
        url="/classes/account/",
    )
    return ScheduledOccurrence(
        event_key="class_reminder",
        anchor=session.starts_at,
        offset=timedelta(hours=-hours_before),
        context={"member": registration.member},
        period=f"reg:{registration.pk}:reminder:{session.pk}",
        actor=registration.member.user if registration.member is not None else None,
        target=registration,
        title="Class reminder",
        body=f"{offering.title} starts soon.",
        url="/classes/account/",
        messages={Channel.EMAIL: email_message},
        email_to=registration.email,
    )


def send_reminder_email(registration: "Registration", session: "ClassSession") -> None:
    """Emit a registrant's session reminder NOW: reminder email + one in-app row.

    Thin wrapper that fires :func:`build_class_reminder_occurrence` immediately,
    bypassing the timing gate (used where the reminder is sent on demand rather than
    walked by the scheduler). One ``class_reminder`` event replaces the old dedicated
    send + the task's suppressed in-app ``dispatch``: the preserved reminder email
    goes to ``registration.email`` via ``email_to`` while the ``registrant`` resolver
    posts the in-app row. The dedupe ``period`` makes it idempotent per (registration,
    session) regardless of how it's invoked.

    ``hours_before`` is irrelevant to an immediate send (the occurrence is fired
    directly, not due-checked), so any value yields the same email/in-app result.
    """
    occurrence = build_class_reminder_occurrence(registration, session, hours_before=0)
    occurrence.fire()
