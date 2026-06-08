"""Single choke point for transactional email — sends and audits every attempt.

Every transactional email in the app routes through ``send()`` so a
``TransactionalEmailLog`` row is written whether the send succeeds or fails.
The returned row can be attached to a ``SiteActivity`` via its ``email_log`` FK.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail

from core.models import TransactionalEmailLog


def send(
    *,
    to: str | list[str],
    subject: str,
    trigger_kind: str,
    text_body: str,
    html_body: str | None = None,
    from_email: str | None = None,
    best_effort: bool = False,
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

    Returns:
        The TransactionalEmailLog row written for this attempt.

    Raises:
        Exception: Re-raises the underlying send error unless best_effort=True.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    joined = ", ".join(recipients)
    try:
        send_mail(
            subject=subject,
            message=text_body,
            from_email=from_email or settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            html_message=html_body,
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
