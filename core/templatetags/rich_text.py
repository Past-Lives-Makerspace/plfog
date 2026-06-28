"""Template filters for rendering admin-authored rich-text email bodies.

The single safe-render choke point for a stored email body. Load with
``{% load rich_text %}`` in an email template and pipe the body through the matching
filter — ``rich_email_body`` for the ``.html`` part, ``rich_email_text`` for ``.txt``.
Both sanitize before rendering, so an unsanitized value can never reach a recipient.
"""

from __future__ import annotations

from django import template
from django.utils.safestring import SafeString, mark_safe

from core.html_sanitize import render_rich_email_body, render_rich_email_text

register = template.Library()


@register.filter(name="rich_email_body")
def rich_email_body(value: str | None) -> SafeString:
    """Render a stored body as sanitized, inline-styled, email-ready HTML."""
    return mark_safe(render_rich_email_body(value or ""))


@register.filter(name="rich_email_text")
def rich_email_text(value: str | None) -> SafeString:
    """Render a stored body as readable plain text for the ``.txt`` email part."""
    return mark_safe(render_rich_email_text(value or ""))
