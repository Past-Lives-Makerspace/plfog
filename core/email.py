"""Single choke point for transactional email — sends and audits every attempt.

Every transactional email in the app routes through ``send()`` so a
``TransactionalEmailLog`` row is written whether the send succeeds or fails.
The returned row can be attached to a ``SiteActivity`` via its ``email_log`` FK.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail

from core.models import TransactionalEmailLog

# An attachment as (filename, content, mimetype) — e.g. ("orientation.ics", ics_bytes, "text/calendar").
Attachment = tuple[str, str | bytes, str]


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
) -> None:
    """Hand the message to Django's mail backend.

    Uses the plain ``send_mail`` path when there are no attachments, no BCC, and no
    category (unchanged behaviour for the vast majority of sends); switches to
    ``EmailMultiAlternatives`` when attachments, a BCC list, OR a category are
    present, since ``send_mail`` cannot carry any of the three. The BCC list
    preserves recipient privacy for bulk sends (instructor/admin class emails BCC
    every registrant); the category rides an ``X-Category`` header so the ESP and
    mail-client rules can filter by workflow.
    """
    if not attachments and not bcc and not category:
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
    if html_body:
        message.attach_alternative(html_body, "text/html")
    for filename, content, mimetype in attachments or []:
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

    Returns:
        The TransactionalEmailLog row written for this attempt.

    Raises:
        Exception: Re-raises the underlying send error unless best_effort=True.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    bcc_list = [bcc] if isinstance(bcc, str) else list(bcc or [])
    joined = ", ".join(recipients + bcc_list)
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
