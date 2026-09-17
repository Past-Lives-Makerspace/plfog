"""Outbound class-related emails."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.template.loader import render_to_string

from core import email as core_email
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from classes.models import ClassApproval, ClassOffering, ClassSession, Registration
    from core.events.emit import EmitResult
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
    """Turn a relative path into an absolute URL using the book site base URL.

    Thin delegate — the public helper lives in :func:`core.urls_util.book_absolute_url`
    so other apps don't import a private classes helper. Kept for this module's
    many internal call sites.
    """
    from core.urls_util import book_absolute_url

    return book_absolute_url(path)


def _flat_text_email_html(text: str) -> str:
    """Wrap a plain-text notice body in the branded email shell.

    The instructor/admin new-registration notices assemble their bodies as plain text.
    Values are HTML-escaped, blank-line-separated blocks become paragraphs and single
    newlines become ``<br>``, so the result is the same dark branded card the rest of
    our emails use instead of a bare plain-text message.
    """
    from django.utils.html import escape

    from core.events.templates import wrap_email_html

    blocks = [block for block in text.split("\n\n") if block.strip()]
    fragment = "".join("<p>" + escape(block).replace("\n", "<br>") + "</p>" for block in blocks)
    return wrap_email_html(fragment)


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
    class_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
    template_context = {
        "registration": registration,
        "offering": offering,
        "upcoming_sessions": upcoming_sessions,
        "self_serve_url": self_serve_url,
        "class_url": class_url,
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
    class_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
    context = {
        "offering": offering,
        "greeting_name": greeting_name,
        "upcoming_sessions": upcoming_sessions,
        "self_serve_url": self_serve_url,
        "class_url": class_url,
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
    manage_url = _absolute_url(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk}))
    template_context = {
        "registration": registration,
        "offering": offering,
        "class_url": _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})),
        "manage_url": manage_url,
        "amount_paid": f"{registration.amount_paid_cents / 100:.2f}",
        "spots_filled": offering.registrations.count(),
        "capacity": offering.capacity,
    }
    # No trigger_kind → emit labels the audit row with the event key
    # (instructor_new_registration), one vocabulary so the log joins to the event + prefs.
    email_message = Message(
        title=subject,
        body=render_to_string("classes/emails/instructor_new_registration.txt", template_context),
        url=manage_url,
        html_body=render_to_string("classes/emails/instructor_new_registration.html", template_context),
    )
    emit(
        "instructor_new_registration",
        context={"offering": offering},
        title="New registration",
        body=offering.title,
        url=manage_url,
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
    class_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
    subject = f"[Classes] New registration: {registration.first_name} {registration.last_name} — {offering.title}"
    body = (
        f"{registration.first_name} {registration.last_name} ({registration.email}) "
        f'registered for "{offering.title}" (instructor: {offering.instructor.display_name if offering.instructor else "N/A"}).\n\n'
        f"Status: {registration.get_status_display()}\n"
        f"Paid: ${registration.amount_paid_cents / 100:.2f}\n"
        f"Capacity: {offering.registrations.count()}/{offering.capacity}\n\n"
        f"View the class: {class_url}"
    )
    # No trigger_kind → emit labels the audit row with the event key (one vocabulary).
    email_message = Message(title=subject, body=body, html_body=_flat_text_email_html(body))
    emit(
        "instructor_new_registration",
        target=registration,
        context={"instructor": None},
        messages={Channel.EMAIL: email_message},
        email_to=admin_emails,
        period=f"reg:{registration.pk}:admin_notice",
    )


def _token_review_url(row: "ClassApproval") -> str:
    """The tokenized reviewer page for ``row`` — a guild lead reviews without a hub login.

    Only the guild-lead lane is emailed a bearer link. The admin lane is a CMS Administrator
    who is already a logged-in staff member, so it is sent to :func:`_admin_review_url`
    instead and never carries a token.
    """
    return _absolute_url(reverse("classes:class_review", kwargs={"token": row.token}))


def _admin_review_url(offering: "ClassOffering") -> str:
    """The logged-in admin review screen for ``offering`` (relative path).

    ``/classes/admin/<pk>/review/`` runs behind ``class_screen_required``, which asks for a
    login and then for ``ClassAccess.can_approve``. A CMS Administrator holds the
    ``CLASS_APPROVER`` grant, which carries ``can_approve`` on every class, so the screen
    opens for them — the reason the admin lane needs no bearer token at all.
    """
    return reverse("classes:admin_class_review", kwargs={"pk": offering.pk})


def _guild_lead_lane_status(offering: "ClassOffering") -> str:
    """One plain sentence naming where the guild lead's room check stands, for the admin.

    Both lanes open at submit, so the admin is always reading a lane that is already open
    and needs to be told what it says. Returns ``""`` for a category with no guild lead,
    where there is no second lane to report on. The lookup is exhaustive over ``Decision``
    on purpose: an unmapped value raises rather than reporting the wrong answer to the one
    person who can publish the class.
    """
    from classes.models import ClassApproval

    row = offering.approvals.filter(role=ClassApproval.Role.GUILD_LEAD).order_by("created_at").last()
    if row is None:
        return ""
    return {
        "": "The guild lead has not answered yet.",
        ClassApproval.Decision.APPROVED: "The guild lead has already said the space is free.",
        ClassApproval.Decision.CHANGES_REQUESTED: "The guild lead has asked the instructor for changes.",
        ClassApproval.Decision.DENIED: "The guild lead has turned these dates down.",
        ClassApproval.Decision.OVERRIDDEN_BY_ADMIN: "The guild lead's check was closed when an admin published.",
    }[row.decision]


def _emit_review_request(
    offering: "ClassOffering",
    row: "ClassApproval",
    *,
    recipients: list[str],
    role_label: str,
    guild: "Guild | None",
    instructor_name: str,
    event_key: str,
    review_url: str,
    in_app_url: str,
    period: str = "",
) -> "EmitResult":
    """Emit one reviewer lane's request: review email + in-app row, on ``event_key``.

    Both lanes open at submit and each carries its own event, because each is a different
    audience with a different opt-out. The guild-lead lane rides ``class_review_requested``
    (the guild's whole leadership, via the ``GUILD_LEADERSHIP_OR_CLASS_APPROVERS`` resolver
    against ``guild``); the admin lane rides ``class_validation_requested``, which is
    STAFF_ONLY and resolves to the CLASS_APPROVER holders, so a CMS Administrator's "Class
    needs executive validation" opt-out governs both of the admin's emails.

    ``review_url`` and ``in_app_url`` are the caller's, not this function's: the guild lead
    gets the tokenized page they can open without a hub login, the admin gets
    ``/classes/admin/<pk>/review/``. ``period`` defaults to the one-shot request bucket; the
    Remind lead action passes a dated bucket so a reminder delivers once per day and dedupes
    after that.

    The email is the ``review_request.{txt,html}`` shell addressed to the exact ``recipients``
    list via ``email_to``. No-op on the email when there are no recipients; the in-app still
    fans out to whoever the resolver finds.
    """
    from classes.models import ClassApproval
    from core.events.senders import emit_with_email_shell

    guild_name = guild.name if guild is not None else ""
    template_context = {
        "offering": offering,
        "approval": row,
        "review_url": review_url,
        "role_label": role_label,
        # Only the admin is told where the other lane stands: they are the one who can
        # publish over it, and the guild lead reading their own lane learns nothing.
        "guild_lead_status": _guild_lead_lane_status(offering) if row.role == ClassApproval.Role.ADMIN else "",
    }
    return emit_with_email_shell(
        event_key,
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
        url=in_app_url,
        email_to=recipients or None,
        period=period or f"approval:{row.pk}:request",
    )


def send_guild_lead_review_reminder(row: "ClassApproval") -> "EmitResult | None":
    """Remind lead: re-send the guild-lead review request for an open gate, once per day.

    The same ``class_review_requested`` email and bell row as the original request, on
    the dated period ``approval:{pk}:reminder:{today}`` so a second click the same day
    lands in ``EmitResult.skipped_duplicates`` and tomorrow's click delivers again. The
    instructor explainer is deliberately NOT re-sent (the instructor already knows).
    Returns ``None`` when the guild has no lead or staff left to remind.
    """
    offering = row.class_offering
    guild = offering.category.guild if offering.category_id else None
    recipients = _guild_leadership_recipients(guild)
    if not recipients:
        return None
    instructor_name = offering.instructor.display_name if offering.instructor is not None else "An instructor"
    today = timezone.localdate().isoformat()
    return _emit_review_request(
        offering,
        row,
        recipients=recipients,
        role_label="Guild Lead",
        guild=guild,
        instructor_name=instructor_name,
        event_key="class_review_requested",
        review_url=_token_review_url(row),
        in_app_url="/classes/teach/",
        period=f"approval:{row.pk}:reminder:{today}",
    )


def _emit_instructor_review_explainer(offering: "ClassOffering", row: "ClassApproval") -> None:
    """Email the instructor that their class is in review (email-only, no bell row).

    Exactly one of these goes out per submission, keyed on the admin row — see
    :func:`send_review_requests`. Both reviewer lanes open at once, so an explainer per lane
    would land two identical "your class is in review" emails in the instructor's inbox on
    every guilded submit; the template names both reviewers instead
    (``offering.first_gate_label``), so one email still tells the whole story.

    Routes the preserved ``review_submitted_instructor.{txt,html}`` shell through the
    spine as the ``class_review_requested`` EMAIL channel, addressed to the instructor's
    ``primary_email`` via ``email_to``. ``recipient_user_ids=set()`` empties the
    resolver fan-out entirely: with a ``None`` guild the event's composed resolver
    returns the CLASS_APPROVER capability holders, and before this guard they each got
    a bell row, push, and Discord DM rendered from the generic copy ("Hi [missing:
    member_name]") carrying the instructor's edit link. The email's audit label is the
    event key (``class_review_requested``), and its ``email:<instructor>`` dedup ref
    keeps it independent of the reviewer email. No-op without an instructor email.
    """
    if not (offering.instructor and offering.instructor.primary_email):
        return
    from classes.models import ClassApproval
    from core.events.senders import emit_with_email_shell

    instructor_url = _absolute_url(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
    template_context = {
        "offering": offering,
        "approvals": [row],
        "instructor_url": instructor_url,
        # Both reviewers are named from ``first_gate_label``; this says whether there are two
        # of them, so the copy can explain the split without re-deriving it from the phrase.
        "has_guild_lead_lane": ClassApproval.Role.GUILD_LEAD in offering.required_review_roles,
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
        recipient_user_ids=set(),
    )


def send_guild_lead_review_request(offering: "ClassOffering", approval: "ClassApproval") -> None:
    """The guild-lead lane's request: review email + in-app row to the guild's leadership.

    One ``class_review_requested`` event sends the dedicated review email to the category's
    guild leadership (lead plus every staff member — they share review duties) AND posts the
    in-app row to that same leadership, so opted-in leadership get exactly one email and one
    bell row. When no leadership has an email, only the bell rows go out.

    The instructor's explainer is NOT sent here. Both lanes open together, and the explainer
    belongs to the submission rather than to either lane — :func:`send_review_requests` emits
    it once, keyed on the admin row.
    """
    guild = offering.category.guild if offering.category_id else None
    recipients = _guild_leadership_recipients(guild)
    instructor_name = offering.instructor.display_name if offering.instructor is not None else "An instructor"
    _emit_review_request(
        offering,
        approval,
        recipients=recipients,
        role_label="Guild Lead",
        guild=guild,
        instructor_name=instructor_name,
        event_key="class_review_requested",
        review_url=_token_review_url(approval),
        in_app_url="/classes/teach/",
    )


def send_admin_review_request(offering: "ClassOffering", approval: "ClassApproval") -> None:
    """The admin lane's request at submit: review email + in-app row to the CMS Administrators.

    This lane opens on EVERY submission, not only on a category with no guild lead, because
    both gates now open together. It rides ``class_validation_requested``: STAFF_ONLY,
    resolving to the CLASS_APPROVER holders, and already carrying the CMS Administrator's
    "Class needs executive validation" opt-out — so one switch governs both of the admin's
    review emails rather than two that can disagree.

    No bearer token is emailed here. The reviewer is a logged-in CMS Administrator whose
    grant opens ``/classes/admin/<pk>/review/`` on any class, so both the email link and the
    bell row go there. The submit-time bucket is ``approval:<pk>:request``, deliberately
    distinct from :func:`send_admin_validation_request`'s ``approval:<pk>:validation`` — the
    two now share an event key and a row, so only the period keeps one from swallowing the
    other.

    The instructor's explainer is emitted by :func:`send_review_requests`, not here.
    """
    instructor_name = offering.instructor.display_name if offering.instructor is not None else "An instructor"
    _emit_review_request(
        offering,
        approval,
        recipients=[],
        role_label="Admin",
        guild=None,
        instructor_name=instructor_name,
        event_key="class_validation_requested",
        review_url=_absolute_url(_admin_review_url(offering)),
        in_app_url=_admin_review_url(offering),
    )


def send_review_requests(offering: "ClassOffering", rows: list["ClassApproval"]) -> None:
    """Notify every reviewer a submission opened, and the instructor exactly once.

    Called by ``ClassOffering._notify_reviewers`` with the rows ``submit_for_review`` just
    created. Each lane gets its own request through its own event and its own audience; the
    instructor gets ONE explainer, keyed on the admin row.

    That last part is the whole reason this orchestrator exists. Both lane senders used to
    end with the instructor explainer, which was correct while exactly one of them fired per
    submission. Now that both fire, a per-lane explainer would land two identical "Your class
    is in review" emails on every guilded submit — the ledger cannot collapse them, because
    each carries a bucket keyed on a different approval row. The admin row is the key because
    every submission has one; the guild-lead row is optional.
    """
    from classes.models import ClassApproval

    for row in rows:
        if row.role == ClassApproval.Role.GUILD_LEAD:
            send_guild_lead_review_request(offering, row)
        else:
            send_admin_review_request(offering, row)
    # Indexed, not searched: a submission without an admin gate is not a shape this app has,
    # and a missing key should raise here rather than quietly skip the instructor's email.
    _emit_instructor_review_explainer(offering, {row.role: row for row in rows}[ClassApproval.Role.ADMIN])


def send_admin_validation_request(offering: "ClassOffering", approval: "ClassApproval") -> None:
    """Tell the admins the guild lead has signed off and theirs is the last gate left.

    Fired from ``ClassOffering.notify_admins_of_guild_lead_approval`` when a Guild Lead approves while the
    admin's lane is still open. That lane has been open since submit and was already emailed
    once (:func:`send_admin_review_request`), so this is news about the OTHER lane, not the
    opening of this one.

    One ``class_validation_requested`` event carries the
    ``admin_validation_request.{txt,html}`` shell; the email and the in-app row both ride the
    CLASS_APPROVERS resolver, so the CMS Administrators (holders only) get it.
    ``class_validation_requested`` logs no SiteActivity, so the emit introduces no
    activity-row duplication.

    ``approval:<pk>:validation`` is deliberately a different bucket from the submit-time
    ``approval:<pk>:request``. Both emails now ride the same event key against the same row,
    so the period is the only thing keeping the second delivery from being read as a repeat
    of the first and dropped.
    """
    from core.events.senders import emit_with_email_shell

    review_path = _admin_review_url(offering)
    guild = offering.category.guild if offering.category_id else None
    lead = guild.guild_lead if guild else None
    template_context = {
        "offering": offering,
        "approval": approval,
        "review_url": _absolute_url(review_path),
        "guild_lead_name": lead.display_name if lead is not None else "A guild lead",
        "instructor_name": offering.instructor.display_name if offering.instructor is not None else "the instructor",
    }
    lead_name = template_context["guild_lead_name"]
    emit_with_email_shell(
        "class_validation_requested",
        target=offering,
        context={},
        subject=f"Last approval needed: {offering.title}",
        text_template="classes/emails/admin_validation_request.txt",
        html_template="classes/emails/admin_validation_request.html",
        template_context=template_context,
        in_app_title="A class is waiting on your approval",
        in_app_body=f"{lead_name} says the space is free. Yours is the last approval this class needs.",
        url=review_path,
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

    ``held_for_room_check`` is the admin's second approve action ("Approve, hold for the room
    check"): the admin has said yes and published nothing, so the guild lead's answer on the
    space is what takes the class live. It reads very differently from the guild lead
    approving while the admin has not answered, and the instructor needs to know which of the
    two they are waiting on, so the template is told rather than left to infer it.
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
        "held_for_room_check": (
            row.role == ClassApproval.Role.ADMIN
            and row.decision == ClassApproval.Decision.APPROVED
            and not fully_approved
            and any(pending.role == ClassApproval.Role.GUILD_LEAD for pending in pending_rows)
        ),
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
    class_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
    template_context = {
        "registration": registration,
        "offering": offering,
        "position": registration.waitlist_position,
        "self_serve_url": self_serve_url,
        "class_url": class_url,
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
    class_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
    settings_obj = ClassSettings.load()
    template_context = {
        "registration": registration,
        "offering": offering,
        "register_url": register_url,
        "class_url": class_url,
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


def _promoted_template_context(registration: "Registration") -> dict[str, object]:
    """Shared template context for the two promoted-from-waitlist emails."""
    offering = registration.class_offering
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    return {
        "registration": registration,
        "offering": offering,
        "upcoming_sessions": upcoming_sessions,
        "class_url": _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})),
        "self_serve_url": _absolute_url(
            reverse("classes:my_registration", kwargs={"token": registration.self_serve_token})
        ),
        "pay_url": _absolute_url(
            reverse("classes:my_registration_pay", kwargs={"token": registration.self_serve_token})
        ),
        "amount_due_dollars": f"{registration.balance_due_cents / 100:.2f}",
    }


def send_waitlist_promoted(registration: "Registration") -> None:
    """Emit the plain "You're in" notice after a staff promote (no payment link).

    Sent for free promotes (computed due of 0) and when staff picks "Not now" on
    the pay-link follow-up. The ``reg:{pk}:promoted`` period delivers it once
    ever — the modal-close fallback can never stack a second copy.
    """
    from core.events.senders import emit_with_email_shell

    offering = registration.class_offering
    template_context = _promoted_template_context(registration)
    emit_with_email_shell(
        "waitlist_promoted",
        actor=registration.member.user if registration.member is not None else None,
        target=registration,
        context={"member": registration.member},
        subject=f"You're in! {offering.title}",
        text_template="classes/emails/promoted.txt",
        html_template="classes/emails/promoted.html",
        template_context=template_context,
        in_app_title="You're in the class",
        in_app_body=offering.title,
        url="/classes/account/",
        email_to=registration.email,
        period=f"reg:{registration.pk}:promoted",
    )


def send_payment_link_email(registration: "Registration", actor: "User | None") -> None:
    """Send (or deliberately re-send) the "You're in! Complete your payment" email.

    Emits the ``waitlist_promoted_pay`` event with the pay-page CTA, stamps
    ``payment_link_sent_at``, and logs PAYMENT_LINK_SENT attributed to ``actor``.
    The minute-bucketed period collapses a double-click into one delivery while a
    deliberate re-send in a later minute delivers again.

    Raises:
        RegistrationStateError: If the registration has no outstanding balance.
    """
    from classes import activity
    from classes.exceptions import RegistrationStateError
    from classes.models import CmsActivity
    from core.events.senders import emit_with_email_shell

    if not registration.is_unpaid:
        raise RegistrationStateError("This registration has no outstanding balance.")
    offering = registration.class_offering
    template_context = _promoted_template_context(registration)
    now = timezone.now()
    emit_with_email_shell(
        "waitlist_promoted_pay",
        actor=registration.member.user if registration.member is not None else None,
        target=registration,
        context={"member": registration.member},
        subject=f"You're in! Complete your payment for {offering.title}",
        text_template="classes/emails/promoted_pay.txt",
        html_template="classes/emails/promoted_pay.html",
        template_context=template_context,
        in_app_title="You're in — complete your payment",
        in_app_body=offering.title,
        url="/classes/account/",
        email_to=registration.email,
        period=f"reg:{registration.pk}:paylink:{now:%Y%m%d%H%M}",
    )
    registration.payment_link_sent_at = now
    registration.save(update_fields=["payment_link_sent_at"])
    activity.log(
        CmsActivity.Kind.PAYMENT_LINK_SENT,
        class_offering=offering,
        registration=registration,
        actor=actor,
    )


def send_removal_notice(registration: "Registration", *, was_waitlisted: bool) -> None:
    """Emit the staff-removal notice — seat-holder and waitlist variants, one template pair.

    Called only from ``Registration.remove_by_staff`` so self-serve cancels and
    refund flows keep their current email behavior. The waitlist variant carries
    no seat, cancellation-of-a-confirmed-spot, or refund language — none of it is
    true for someone who never held a seat.
    """
    from core.events.senders import emit_with_email_shell

    offering = registration.class_offering
    if was_waitlisted:
        subject = f"You've been removed from the waitlist for {offering.title}"
        in_app_title = "Removed from a waitlist"
    else:
        subject = f"Your registration for {offering.title} was cancelled"
        in_app_title = "Your registration was cancelled"
    template_context = {
        "registration": registration,
        "offering": offering,
        "was_waitlisted": was_waitlisted,
        "show_refund_note": not was_waitlisted and registration.amount_paid_cents > 0,
        "amount_paid_dollars": f"{registration.amount_paid_cents / 100:.2f}",
        "class_url": _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})),
        "browse_url": _absolute_url(reverse("classes:public_list")),
    }
    emit_with_email_shell(
        "registration_removed",
        actor=registration.member.user if registration.member is not None else None,
        target=registration,
        context={"member": registration.member},
        subject=subject,
        text_template="classes/emails/removed.txt",
        html_template="classes/emails/removed.html",
        template_context=template_context,
        in_app_title=in_app_title,
        in_app_body=offering.title,
        url="/classes/account/",
        email_to=registration.email,
        period=f"reg:{registration.pk}:removed",
    )


def send_registration_moved(registration: "Registration", *, source: "ClassOffering") -> None:
    """Tell a registrant that staff reassigned them from one class to another.

    Fired from :meth:`Registration.move_to`, the single move path both the teaching
    portal and the admin registrations tab go through. Nobody asked for this move, and
    the confirmation they are holding now names the wrong class, so the email leads with
    both classes by name and carries the new schedule plus a link to the new class page.

    Payment is deliberately not mentioned: ``move_to`` reconciles no money (a $60 seat
    moved into a $45 class stays paid at $60), so any figure here would be a promise the
    system does not keep. Staff settle the difference by hand and say so themselves.

    Args:
        registration: The moved registration, already pointing at its new class.
        source: The class it came FROM — read before the move, since the row no
            longer references it.
    """
    from core.events.senders import emit_with_email_shell

    offering = registration.class_offering
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    template_context = {
        "registration": registration,
        "offering": offering,
        "source": source,
        "upcoming_sessions": upcoming_sessions,
        "class_url": _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})),
        "self_serve_url": _absolute_url(
            reverse("classes:my_registration", kwargs={"token": registration.self_serve_token})
        ),
        "is_waitlisted": registration.status == registration.Status.WAITLISTED,
    }
    emit_with_email_shell(
        "registration_moved",
        actor=registration.member.user if registration.member is not None else None,
        target=registration,
        context={"member": registration.member},
        subject=f"You've been moved to {offering.title}",
        text_template="classes/emails/moved.txt",
        html_template="classes/emails/moved.html",
        template_context=template_context,
        in_app_title="You've been moved to another class",
        in_app_body=f"{source.title} to {offering.title}",
        url="/classes/account/",
        email_to=registration.email,
        # Each move is its own notice: a student moved twice hears about it twice, and
        # the pair of class pks makes the second move a different bucket from the first.
        period=f"reg:{registration.pk}:moved:{source.pk}:{offering.pk}",
    )


def send_duplicate_payment_alert(
    registration: "Registration", *, amount_cents: int, payment_intent: str, session_id: str
) -> None:
    """Alert the admins that a balance payment landed AFTER the row was already settled.

    The studio has collected twice and a refund is owed — silence is unacceptable.
    Flat-text body wrapped in the branded shell, addressed to the admin rails.
    """
    admin_emails = _admin_recipients()
    if not admin_emails:
        return
    offering = registration.class_offering
    detail_url = _absolute_url(reverse("classes:admin_registration_detail", kwargs={"pk": registration.pk}))
    stripe_url = f"https://dashboard.stripe.com/payments/{payment_intent}"
    name = f"{registration.first_name} {registration.last_name}".strip() or registration.email
    body = (
        f"{name} ({registration.email}) paid ${amount_cents / 100:.2f} online for "
        f'"{offering.title}" AFTER the balance was already settled (marked paid by staff, '
        f"or an earlier payment landed first).\n\n"
        f"A refund is owed for one of the two payments.\n\n"
        f"Registration: {detail_url}\n"
        f"Stripe payment: {stripe_url}\n"
        f"Checkout session: {session_id}"
    )
    core_email.send(
        to=admin_emails,
        subject=f"Duplicate payment: {name}, {offering.title}",
        trigger_kind="classes.duplicate_payment_alert",
        text_body=body,
        html_body=_flat_text_email_html(body),
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
    class_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
    template_context = {
        "registration": registration,
        "session": session,
        "offering": offering,
        "self_serve_url": self_serve_url,
        "class_url": class_url,
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
