"""Send-time copy resolution — DB copy first, seeded default as fallback (§2.3, P3).

When :func:`core.events.emit.emit` is asked to render copy for an event/channel, it
calls :func:`rendered_message` here. The resolution is:

1. the admin-edited :class:`core.models.NotificationTemplate` row for
   ``(event_key, channel)``, if one exists; else
2. the **seeded default** copy from :mod:`core.events.copy` (so a never-seeded DB
   still produces serviceable copy);

then the constrained :mod:`core.events.rendering` substitution fills the documented
merge fields from the supplied context (a context value missing → visible marker).

ADDITIVE: only the new ``emit()`` path consumes this. Existing senders keep their own
templates untouched until Phase 4. ``emit()`` only renders from copy when a caller
does NOT pass an explicit ``title``/``body`` (the Phase-1 incremental path still
works), so wiring this in changes no existing behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.template.loader import render_to_string

from core.events import copy as copy_module
from core.events import rendering
from core.events.registry import Channel

if TYPE_CHECKING:
    from core.events.channels import Message

# Email channels whose HTML body is wrapped in the branded shell at render time.
_EMAIL_CHANNELS = (Channel.EMAIL, Channel.SCHEDULED_EMAIL)


def _db_copy(event_key: str, channel: Channel):  # type: ignore[no-untyped-def]
    """The admin-edited NotificationTemplate row for (event, channel), or ``None``.

    Lazy + defensive: imported inside the function (model layer not touched at import
    time) and tolerant of the table not yet existing (pre-migration). Copy resolution
    is best-effort — a DB hiccup falls back to the seeded default, never an exception.
    """
    try:
        from core.models import NotificationTemplate

        return NotificationTemplate.objects.filter(event_key=event_key, channel=channel.value).first()
    except Exception:  # pragma: no cover - defensive: DB unavailable / table missing
        return None


def resolved_copy(event_key: str, channel: Channel) -> tuple[str, str, str]:
    """The raw (subject, body_text, body_html) copy for an event/channel.

    DB row wins; otherwise the seeded default for the channel. Returned UNrendered
    (placeholders intact) — the caller renders against a context.
    """
    row = _db_copy(event_key, channel)
    if row is not None:
        return row.subject, row.body_text, row.body_html
    default = copy_module.default_copy_for(event_key, channel)
    return default.subject, default.body_text, default.body_html


def rendered_copy(event_key: str, channel: Channel, context: dict[str, Any]) -> rendering.RenderedCopy:
    """Resolve the copy for an event/channel and render it against ``context``."""
    subject, body_text, body_html = resolved_copy(event_key, channel)
    return rendering.render_copy(subject=subject, body_text=body_text, body_html=body_html, context=context)


def wrap_email_html(fragment: str) -> str:
    """Render a copy-mode HTML fragment inside the branded email shell.

    ``fragment`` is the output of :func:`core.events.rendering.render_html` — trusted
    admin-authored literal markup with every interpolated *value* already HTML-escaped.
    The shell template marks it ``|safe``, so wrapping re-escapes nothing: the shell is
    trusted and the merge values stay escaped end to end.

    Returns a *plain* ``str`` (not a ``SafeString``): ``render_to_string`` returns a
    ``SafeString``, but the admin preview renders this into ``srcdoc="{{ wrapped_html }}"``
    and Django does NOT attribute-escape a ``SafeString`` — leaving raw quotes that would
    truncate the attribute and break the iframe. Coercing to a plain ``str`` (``"" +``)
    makes ``{{ wrapped_html }}`` attribute-escape it correctly; the email send path is
    unaffected by the ``str`` / ``SafeString`` distinction.
    """
    return "" + render_to_string("membership/emails/notification_shell.html", {"body_html": fragment})


def rendered_message(event_key: str, channel: Channel, context: dict[str, Any], *, url: str = "") -> "Message":
    """Build a :class:`core.events.channels.Message` from DB/seeded copy + context.

    The rendered subject becomes the message ``title``; the rendered text body
    becomes ``body``; the rendered HTML body becomes ``html_body`` (or ``None`` when
    blank). ``trigger_kind`` is the event key (the audit label the email choke-point
    and Discord embed use).

    For the email channels the HTML body is the unbranded copy *fragment*, so it is
    wrapped in the branded shell here — the single choke point that covers both email
    send paths (per-recipient ``EmailAdapter`` and explicit-address ``email_to``).
    The text body, in-app/Discord channels, and an empty HTML body are untouched.
    """
    from core.events.channels import Message

    rendered = rendered_copy(event_key, channel, context)
    html = rendered.body_html or None
    if html is not None and channel in _EMAIL_CHANNELS:
        html = wrap_email_html(html)
    return Message(
        title=rendered.subject,
        body=rendered.body_text,
        url=url,
        html_body=html,
        trigger_kind=event_key,
    )


def preview_context(event_key: str) -> dict[str, str]:
    """The sample context used to drive the live preview for ``event_key``."""
    return copy_module.sample_context_for(event_key)
