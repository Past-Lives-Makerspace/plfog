"""Template filter for rendering guild Markdown to sanitized HTML."""

from __future__ import annotations

from django import template
from django.utils.safestring import SafeString, mark_safe

from membership.markdown import render_markdown

register = template.Library()


@register.filter(name="guild_markdown")
def guild_markdown(value: str) -> SafeString:
    """Render guild Markdown to sanitized HTML, safe to display.

    Safe ONLY because ``render_markdown`` sanitizes the output with a tight
    allowlist — never apply ``|safe`` to raw Markdown source directly.
    """
    return mark_safe(render_markdown(value or ""))  # noqa: S308 — render_markdown sanitizes


@register.filter(name="help_markdown")
def help_markdown(value: str) -> SafeString:
    """Render help-center Markdown (admin/repo-authored) to sanitized HTML.

    The ``help`` profile allows ``/static/help/`` images, heading ``id``
    anchors, and same-tab internal links — still fully sanitized, so the same
    mark_safe caveat as ``guild_markdown`` applies.
    """
    return mark_safe(render_markdown(value or "", profile="help"))  # noqa: S308 — render_markdown sanitizes
