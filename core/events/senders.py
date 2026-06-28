"""Phase-4 send helpers — the bridge from a fat-model/service to :func:`emit`.

Every event-driven email in the app used to be a hand-written ``core.email.send``
call paired (often) with an un-suppressed ``notifications.dispatch`` — the
double-send the audit found. Phase 4 replaces each such pair with **one**
:func:`core.events.emit.emit` call: one event → one in-app row + one email
(+ push / Discord per prefs), deduped, audited, recipients resolved centrally.

Two shapes of sender exist, and this module gives each a one-liner:

* :func:`emit_with_email_shell` — the sender's email body is a *rich structural
  template* (session lists, a tokenized link, an ``.ics``). The sender renders that
  shell itself and hands the result in as a per-channel ``Channel.EMAIL`` override,
  so the email content stays **byte-for-byte identical** to today while the in-app
  row + Discord embed render from the DB-editable copy. Attachments ride along.
* plain :func:`emit` (called directly) — the sender's body is a flat string with no
  structure; the DB copy fully expresses it.

This keeps logic in the model/service layer (per CLAUDE.md) while the *plumbing*
(recipient resolution, channel fan-out, dedupe, audit) lives in the spine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.template.loader import render_to_string

from core.events.channels import Message
from core.events.emit import emit
from core.events.registry import Channel

if TYPE_CHECKING:
    from django.db.models import Model

    from core.email import Attachment
    from core.events.emit import EmitResult


def email_shell_message(
    *,
    event_key: str,
    subject: str,
    text_template: str,
    html_template: str | None,
    template_context: dict[str, Any],
    url: str = "",
    email_trigger_kind: str | None = None,
) -> Message:
    """Render a structural email shell into a :class:`~core.events.channels.Message`.

    ``text_template`` / ``html_template`` are the existing ``classes/emails/*`` /
    ``membership/emails/*`` shells — preserved verbatim so the email body (session
    lists, tokenized links, footers) is identical to the pre-migration send. The
    rendered strings become the email-channel Message handed to :func:`emit` as a
    per-channel override.

    ``email_trigger_kind`` overrides the audit label written to
    :class:`core.models.TransactionalEmailLog` for this email. It defaults to the
    ``event_key``, but a migrated sender passes its OLD free-text label (e.g.
    ``"orientations.confirmed"``) so the email audit trail is byte-identical to today
    while the in-app row keeps the event key as its ``trigger``.
    """
    text_body = render_to_string(text_template, template_context)
    html_body = render_to_string(html_template, template_context) if html_template else None
    return Message(
        title=subject,
        body=text_body,
        url=url,
        html_body=html_body or None,
        trigger_kind=email_trigger_kind or event_key,
    )


def emit_with_email_shell(
    event_key: str,
    *,
    actor: Model | None = None,
    target: Model | None = None,
    context: dict[str, Any],
    subject: str,
    text_template: str,
    html_template: str | None,
    template_context: dict[str, Any],
    in_app_title: str = "",
    in_app_body: str = "",
    url: str = "",
    attachments: list[Attachment] | None = None,
    email_to: str | list[str] | None = None,
    email_trigger_kind: str | None = None,
    period: str = "",
) -> EmitResult:
    """Emit an event whose EMAIL channel uses a rich structural shell template.

    The email body is rendered from ``text_template`` / ``html_template`` (the
    existing shell, preserved) and passed as a ``Channel.EMAIL`` override; the in-app
    row + push use ``in_app_title`` / ``in_app_body``; Discord (if the event declares
    it) renders from DB copy. Recipients resolve from the event's registered resolver
    against ``context``. ``attachments`` (e.g. an orientation ``.ics``) ride on the
    email only.

    Args:
        event_key: A registered event key.
        actor / target: For the activity row.
        context: The resolver context (``member`` / ``guild`` / ``booking`` / …).
        subject: The email subject line (matches today's f-string subject).
        text_template / html_template: The structural shell templates.
        template_context: The context the shell templates render against (today's
            email context — registration, offering, sessions, urls, …).
        in_app_title / in_app_body: The bell-row title/body (matches today's
            ``dispatch`` title/body). Empty when the event has no in-app channel.
        url: Click-through URL for the in-app row / push.
        attachments: Optional email attachments (``.ics``).
        email_to: Explicit email recipient address(es) — pass a sender's raw
            ``registration.email`` / invitee address when the dedicated email
            addressed a raw email (preserving exact recipients for guests + aliases).
            When ``None`` the email goes to the resolver-found users.
        email_trigger_kind: Old free-text audit label for the email's
            ``TransactionalEmailLog`` row (defaults to ``event_key``). Preserves the
            pre-migration audit label while the in-app row keeps the event key.
        period: Idempotency bucket.

    Returns:
        The :class:`~core.events.emit.EmitResult`.
    """
    email_message = email_shell_message(
        event_key=event_key,
        subject=subject,
        text_template=text_template,
        html_template=html_template,
        template_context=template_context,
        url=url,
        email_trigger_kind=email_trigger_kind,
    )
    messages = {Channel.EMAIL: email_message}
    channel_attachments = {Channel.EMAIL: attachments} if attachments else None
    return emit(
        event_key,
        actor=actor,
        target=target,
        context=context,
        title=in_app_title,
        body=in_app_body,
        url=url,
        messages=messages,
        attachments=channel_attachments,
        email_to=email_to,
        period=period,
    )
