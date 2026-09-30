"""Template filters for rendering rich-text bodies (email bodies and the class description).

The single safe-render choke point for a stored body. Load with ``{% load rich_text %}`` and
pipe the body through the matching filter — ``rich_email_body`` for an email's ``.html`` part,
``rich_email_text`` for its ``.txt``, ``rich_body`` for a page. Each sanitizes before rendering,
so an unsanitized value can never reach a reader.
"""

from __future__ import annotations

from django import template
from django.utils.safestring import SafeString, mark_safe

from core.html_sanitize import render_rich_body, render_rich_email_body, render_rich_email_text

register = template.Library()


@register.filter(name="rich_email_body")
def rich_email_body(value: str | None) -> SafeString:
    """Render a stored body as sanitized, inline-styled, email-ready HTML."""
    return mark_safe(render_rich_email_body(value or ""))


@register.filter(name="rich_body")
def rich_body(value: str | None) -> SafeString:
    """Render a stored body as sanitized HTML for a page: editor HTML as is, legacy text as paragraphs."""
    return mark_safe(render_rich_body(value or ""))


@register.filter(name="rich_email_text")
def rich_email_text(value: str | None) -> SafeString:
    """Render a stored body as readable plain text for the ``.txt`` email part."""
    return mark_safe(render_rich_email_text(value or ""))
