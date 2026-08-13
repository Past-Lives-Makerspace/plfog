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

import re
from typing import TYPE_CHECKING, Any

from django.template.loader import render_to_string

from core.events import copy as copy_module
from core.events import rendering
from core.events.registry import Channel

if TYPE_CHECKING:
    from core.events.channels import Message
    from core.models import NotificationTemplate

# Email channels whose HTML body is wrapped in the branded shell at render time.
_EMAIL_CHANNELS = (Channel.EMAIL, Channel.SCHEDULED_EMAIL)


def _db_copy(event_key: str, channel: Channel) -> NotificationTemplate | None:
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


# Shell name → (template path, paragraph style, bare-link style). Both shells now
# sit on a light card (the v1.0.0 white-background rebrand): slate #33424F body text
# and navy #0d4876 links read on white; cream/gold would be invisible there.
# Keyed by ``EventType.email_shell``.
_EMAIL_SHELLS: dict[str, tuple[str, str, str]] = {
    "dark": (
        "membership/emails/notification_shell.html",
        "margin:0 0 16px;color:#33424F;font-size:15px;line-height:1.6;",
        "color:#0d4876;text-decoration:underline;font-weight:600;",
    ),
    "light": (
        "membership/emails/notification_shell_light.html",
        "margin:0 0 16px;color:#33424F;font-size:15px;line-height:1.6;",
        "color:#0d4876;text-decoration:underline;font-weight:600;",
    ),
}


def wrap_email_html(fragment: str, *, shell: str = "dark") -> str:
    """Render a copy-mode HTML fragment inside the branded email shell.

    ``fragment`` is the output of :func:`core.events.rendering.render_html` — trusted
    admin-authored literal markup with every interpolated *value* already HTML-escaped.
    The shell template marks it ``|safe``, so wrapping re-escapes nothing: the shell is
    trusted and the merge values stay escaped end to end.

    ``shell`` selects the branded wrapper (``EventType.email_shell``): ``"dark"`` is the
    transactional navy card; ``"light"`` is the hybrid logo-hero-over-white-body shell.
    An unknown name raises ``KeyError`` — fail loudly, same contract as ``get_event``.

    Returns a *plain* ``str`` (not a ``SafeString``): ``render_to_string`` returns a
    ``SafeString``, but the admin preview renders this into ``srcdoc="{{ wrapped_html }}"``
    and Django does NOT attribute-escape a ``SafeString`` — leaving raw quotes that would
    truncate the attribute and break the iframe. Coercing to a plain ``str`` (``"" +``)
    makes ``{{ wrapped_html }}`` attribute-escape it correctly; the email send path is
    unaffected by the ``str`` / ``SafeString`` distinction.
    """
    template, _, _ = _EMAIL_SHELLS[shell]
    return "" + render_to_string(template, {"body_html": _style_copy_fragment(fragment, shell=shell)})


def _style_copy_fragment(fragment: str, *, shell: str = "dark") -> str:
    """Inline-style the unbranded copy fragment so it's readable on its shell's card.

    Copy bodies are authored as bare ``<p>`` / ``<a>`` / ``<strong>`` with no color.
    On the light card a bare ``<a>`` falls back to the client's default link color
    (color does not inherit into anchors), so the link comes out unbranded, and a bare
    ``<p>`` needs its slate color set explicitly. We inject inline styles — which every
    mail client honors, unlike a ``<style>`` block — for exactly the tags the copy uses,
    with the palette picked by ``shell``. Tags the author already styled
    (``<p style=...>``) don't match and are left as-is. Text color is carried by the
    shell's wrapper ``<div>`` (inherited into ``<p>`` / ``<strong>``); the only thing
    that genuinely can't inherit is an ``<a>`` color, so the per-paragraph spacing and
    the link color are the two things injected here.
    """
    _, p_style, a_style = _EMAIL_SHELLS[shell]
    fragment = fragment.replace("<p>", f'<p style="{p_style}">')
    # Only colorize links that DON'T already carry an inline ``style`` — a bare copy
    # link gets the shell's link color, while an already-styled link (e.g. the seeded
    # invite's gold button) is left intact. Prepending a second ``style`` attribute
    # would win over and clobber the original, which silently broke that button.
    return re.sub(
        r"<a (?![^>]*\bstyle=)",
        f'<a style="{a_style}" ',
        fragment,
    )


# Inline styles for the richer rich-text-editor tag set, keyed by tag. The shell's
# wrapper <div> carries the slate text color (inherited into <p>/<strong>); these add
# the margins, sizes, list indentation, and the blockquote rule that mail clients won't
# supply from a stripped <style> block. Headings and bold take a darker navy for
# emphasis on the light card; link navy is handled by _style_copy_fragment.
_RICH_TAG_STYLES = {
    "h2": "margin:24px 0 12px;color:#092E4C;font-size:20px;line-height:1.3;font-weight:700;",
    "h3": "margin:20px 0 10px;color:#092E4C;font-size:17px;line-height:1.35;font-weight:700;",
    "ul": "margin:0 0 16px;padding-left:24px;color:#33424F;font-size:15px;line-height:1.6;",
    "ol": "margin:0 0 16px;padding-left:24px;color:#33424F;font-size:15px;line-height:1.6;",
    "li": "margin:0 0 6px;",
    "blockquote": "margin:0 0 16px;padding:4px 0 4px 16px;border-left:3px solid #0d4876;color:#33424F;font-style:italic;",
    "strong": "color:#092E4C;font-weight:700;",
}


def style_rich_email_fragment(fragment: str) -> str:
    """Inline-style the richer editor tag set so a stored body reads well on the light card.

    Extends :func:`_style_copy_fragment` (which styles ``<p>``/``<a>``) to also cover
    ``h2``/``h3``/``ul``/``ol``/``li``/``blockquote``/``strong``. A tag the sanitizer
    already gave an inline ``style=`` is left untouched, so re-running this is idempotent
    (the announcement path runs the fragment through ``_style_copy_fragment`` again when
    it wraps it in the branded shell).
    """
    fragment = _style_copy_fragment(fragment)
    for tag, style in _RICH_TAG_STYLES.items():
        fragment = re.sub(
            rf"<{tag}(?![^>]*\bstyle=)((?:\s[^>]*)?)>",
            rf'<{tag}\1 style="{style}">',
            fragment,
        )
    return fragment


def rendered_message(event_key: str, channel: Channel, context: dict[str, Any], *, url: str = "") -> "Message":
    """Build a :class:`core.events.channels.Message` from DB/seeded copy + context.

    The rendered subject becomes the message ``title``; the rendered text body
    becomes ``body``; the rendered HTML body becomes ``html_body`` (or ``None`` when
    blank). ``trigger_kind`` is the event key (the audit label the email choke-point
    and Discord embed use).

    For the email channels the HTML body is the unbranded copy *fragment*, so it is
    wrapped in the branded shell here — the single choke point that covers both email
    send paths (per-recipient ``EmailAdapter`` and explicit-address ``email_to``).
    The shell variant comes from the event's registry entry (``EventType.email_shell``).
    The text body, in-app/Discord channels, and an empty HTML body are untouched.
    """
    from core.events.channels import Message
    from core.events.registry import get_event

    rendered = rendered_copy(event_key, channel, context)
    html = rendered.body_html or None
    if html is not None and channel in _EMAIL_CHANNELS:
        html = wrap_email_html(html, shell=get_event(event_key).email_shell)
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
