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

from core.events import copy as copy_module
from core.events import rendering
from core.events.registry import Channel

if TYPE_CHECKING:
    from core.events.channels import Message


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


def rendered_message(event_key: str, channel: Channel, context: dict[str, Any], *, url: str = "") -> "Message":
    """Build a :class:`core.events.channels.Message` from DB/seeded copy + context.

    The rendered subject becomes the message ``title``; the rendered text body
    becomes ``body``; the rendered HTML body becomes ``html_body`` (or ``None`` when
    blank). ``trigger_kind`` is the event key (the audit label the email choke-point
    and Discord embed use).
    """
    from core.events.channels import Message

    rendered = rendered_copy(event_key, channel, context)
    return Message(
        title=rendered.subject,
        body=rendered.body_text,
        url=url,
        html_body=rendered.body_html or None,
        trigger_kind=event_key,
    )


def preview_context(event_key: str) -> dict[str, str]:
    """The sample context used to drive the live preview for ``event_key``."""
    return copy_module.sample_context_for(event_key)
