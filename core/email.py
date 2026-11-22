"""Single choke point for transactional email — sends and audits every attempt.

Every transactional email in the app routes through ``send()`` so a
``TransactionalEmailLog`` row is written whether the send succeeds or fails.
The returned row can be attached to a ``SiteActivity`` via its ``email_log`` FK.

Being the one choke point is also what makes staging containable: ``send()`` applies
:mod:`core.email_policy` (the ``[STAGING]`` marking and the recipient allowlist) before
anything reaches the transport, so no caller can slip a real member an email from a
staging box. Outside staging the policy is a no-op.
"""

from __future__ import annotations

from email.message import Message, MIMEPart
from email.utils import parseaddr
from typing import TYPE_CHECKING

import icalendar
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail

from core import email_policy
from core.models import TransactionalEmailLog

if TYPE_CHECKING:
    from core.email_prefs import StopLink

# An attachment as (filename, content, mimetype) — e.g. ("orientation.ics", ics_bytes, "text/calendar").
# A text mimetype may carry parameters ("text/calendar; method=REQUEST"); see _attachment_part.
Attachment = tuple[str, str | bytes, str]

#: The name calendars show for the organizer of every invite we send (#1287).
INVITE_ORGANIZER_NAME = "Past Lives Makerspace"


def invite_mimetype(method: str) -> str:
    """The Content-Type of a calendar invite attachment: ``text/calendar; method=REQUEST`` or ``CANCEL``.

    Calendars act on the ``method`` parameter: it is what makes the file an invitation, so a
    cancel removes the event and a move updates it rather than adding a second one.
    """
    return f"text/calendar; method={method}"


def add_invite_parties(event: icalendar.Event, *, attendee_email: str, attendee_name: str) -> None:
    """Add the ORGANIZER and ATTENDEE an invitation needs, so calendars apply its updates (#1287).

    The organizer is the address every email goes out from (``DEFAULT_FROM_EMAIL``): Gmail
    ignores a REQUEST or CANCEL whose organizer is not its sender. The attendee is the
    address the email is sent to. An invite must therefore go out with the default From.

    Args:
        event: The VEVENT to add the two lines to.
        attendee_email: The recipient's address.
        attendee_name: The recipient's name, shown as the attendee's CN.
    """
    _name, sender = parseaddr(settings.DEFAULT_FROM_EMAIL)
    organizer = icalendar.vCalAddress(f"mailto:{sender}")
    organizer.params["cn"] = icalendar.vText(INVITE_ORGANIZER_NAME)
    event.add("organizer", organizer)
    attendee = icalendar.vCalAddress(f"mailto:{attendee_email}")
    attendee.params["cn"] = icalendar.vText(attendee_name)
    attendee.params["role"] = icalendar.vText("REQ-PARTICIPANT")
    attendee.params["rsvp"] = icalendar.vText("FALSE")
    event.add("attendee", attendee)


def _attachment_part(filename: str, content: str | bytes, mimetype: str) -> MIMEPart | None:
    """A text attachment whose mimetype carries parameters, as a MIME part; None for any other.

    Django's ``attach(filename, content, mimetype)`` and anymail's Resend backend both expect
    a bare ``type/subtype`` (anymail rejects any other against the file extension), so a
    ``method=`` parameter would be lost or refused. A MIME part keeps the whole Content-Type
    header, and anymail hands that header to Resend as the attachment's ``content_type``.
    """
    header = Message()
    header["Content-Type"] = mimetype
    params = dict((header.get_params() or [])[1:])
    if header.get_content_maintype() != "text" or not params:
        return None
    text = content.decode() if isinstance(content, bytes) else content
    part = MIMEPart()
    part.set_content(
        text, subtype=header.get_content_subtype(), disposition="attachment", filename=filename, params=params
    )
    return part


def _deliver(
    *,
    subject: str,
    text_body: str,
    html_body: str | None,
    from_email: str,
    recipients: list[str],
    attachments: list[Attachment] | None,
    bcc: list[str] | None = None,
    category: str | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    """Hand the message to Django's mail backend.

    Uses the plain ``send_mail`` path when there are no attachments, no BCC, and no
    category (unchanged behaviour for the vast majority of sends); switches to
    ``EmailMultiAlternatives`` when attachments, a BCC list, OR a category are
    present, since ``send_mail`` cannot carry any of the three. The BCC list
    preserves recipient privacy for bulk sends (instructor/admin class emails BCC
    every registrant); the category rides an ``X-Category`` header so the ESP and
    mail-client rules can filter by workflow. ``headers`` are any further headers
    (a past students email's ``List-Unsubscribe``), which also take that path.
    """
    if not attachments and not bcc and not category and not headers:
        send_mail(
            subject=subject,
            message=text_body,
            from_email=from_email,
            recipient_list=recipients,
            html_message=html_body,
        )
        return
    message = EmailMultiAlternatives(
        subject=subject, body=text_body, from_email=from_email, to=recipients, bcc=bcc or None
    )
    if category:
        message.extra_headers["X-Category"] = category
    message.extra_headers.update(headers or {})
    if html_body:
        message.attach_alternative(html_body, "text/html")
    for filename, content, mimetype in attachments or []:
        part = _attachment_part(filename, content, mimetype)
        if part is not None:
            message.attach(part)
        else:
            message.attach(filename, content, mimetype)
    message.send()


def send(
    *,
    to: str | list[str],
    subject: str,
    trigger_kind: str,
    text_body: str,
    html_body: str | None = None,
    from_email: str | None = None,
    best_effort: bool = False,
    attachments: list[Attachment] | None = None,
    bcc: str | list[str] | None = None,
    category: str | None = None,
    stop_link: StopLink | None = None,
) -> TransactionalEmailLog:
    """Send a transactional email and log the attempt.

    Args:
        to: One recipient or a list of them.
        subject: Subject line.
        trigger_kind: Workflow identifier, e.g. "billing.receipt".
        text_body: Plain-text body.
        html_body: Optional HTML alternative.
        from_email: Overrides DEFAULT_FROM_EMAIL when given.
        best_effort: When True, swallow send failures (still logged) instead of
            re-raising. Use for non-critical sends (e.g. notification emails).
        attachments: Optional list of (filename, content, mimetype) tuples, e.g.
            an ``.ics`` calendar invite. Forces the multipart send path.
        bcc: Optional blind-copy recipient(s). Used for bulk sends that must keep
            recipients private (instructor/admin class emails BCC every
            registrant). BCC addresses are recorded in the audit row's ``to_email``
            so the log reflects everyone who received the message.
        category: Optional workflow category (e.g. the event's registry category
            like "Billing" or "Voting"). When set, it rides an ``X-Category``
            header — and forces the multipart send path — so ESP and mail-client
            rules can filter by workflow.
        stop_link: A past students email's stop link (#1042): which instructor it stops and the
            placeholder to fill with this recipient's token. Also adds the one click
            ``List-Unsubscribe`` headers. Nothing else in the body is ever read for it.

    Returns:
        The TransactionalEmailLog row written for this attempt. On staging, when the
        delivery policy suppressed every recipient, no transport call is made and the
        first suppressed row stands in as the attempt.

    Raises:
        Exception: Re-raises the underlying send error unless best_effort=True.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    bcc_list = [bcc] if isinstance(bcc, str) else list(bcc or [])
    # Staging: mark the message and drop every recipient the delivery policy refuses, one
    # log row per drop so the Email Log shows what would have gone out. No-ops elsewhere.
    subject = email_policy.staging_subject(subject)
    text_body = email_policy.staging_text_body(text_body)
    html_body = email_policy.staging_html_body(html_body)
    recipients, suppressed_to = email_policy.partition_recipients(recipients)
    bcc_list, suppressed_bcc = email_policy.partition_recipients(bcc_list)
    suppressed_rows = [
        TransactionalEmailLog.objects.create(
            to_email=address,
            subject=subject,
            trigger_kind=trigger_kind,
            status=TransactionalEmailLog.Status.SUPPRESSED,
            error_message=email_policy.SUPPRESSION_REASON,
        )
        for address in suppressed_to + suppressed_bcc
    ]
    if suppressed_rows and not recipients and not bcc_list:
        return suppressed_rows[0]
    joined = ", ".join(recipients + bcc_list)
    # Personalize the footer's "manage preferences / unsubscribe" link with this
    # recipient's no-login token (a no-op when the footer placeholder is absent).
    from core.email_prefs import finalize_manage_prefs_link, finalize_stop_link

    text_body, html_body = finalize_manage_prefs_link(text_body, html_body, recipients)
    # And the past students stop link (#1042), with a token of its instructor and this recipient's
    # address; the same link rides the one click unsubscribe headers mail clients offer (RFC 8058).
    text_body, html_body, unsubscribe_url = finalize_stop_link(text_body, html_body, recipients, stop_link)
    headers = (
        {"List-Unsubscribe": f"<{unsubscribe_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        if unsubscribe_url
        else None
    )
    try:
        _deliver(
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            from_email=from_email or settings.DEFAULT_FROM_EMAIL,
            recipients=recipients,
            attachments=attachments,
            bcc=bcc_list or None,
            category=category,
            headers=headers,
        )
    except Exception as exc:  # noqa: BLE001 — we log then re-raise unless best_effort
        log = TransactionalEmailLog.objects.create(
            to_email=joined,
            subject=subject,
            trigger_kind=trigger_kind,
            status=TransactionalEmailLog.Status.FAILED,
            error_message=str(exc),
        )
        if not best_effort:
            raise
        return log
    return TransactionalEmailLog.objects.create(
        to_email=joined,
        subject=subject,
        trigger_kind=trigger_kind,
        status=TransactionalEmailLog.Status.SENT,
    )
