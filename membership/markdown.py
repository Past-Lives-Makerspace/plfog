"""Render guild Markdown to sanitized, link-hardened HTML.

The single rendering path for any member-authored Markdown on a guild page
(currently meeting-note bodies). ``render_markdown`` converts Markdown source to
HTML, strips anything outside a tight allowlist (scripts, styles, event handlers,
inline ``style=``, unknown tags), and hardens every surviving link with
``rel``/``target`` so the output is safe to mark safe in a template.
"""

from __future__ import annotations

from typing import Any

import bleach
import markdown as md

# Only these tags survive sanitization. Everything else is stripped (text kept).
_ALLOWED_TAGS = [
    "p",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "strong",
    "em",
    "ul",
    "ol",
    "li",
    "a",
    "code",
    "pre",
    "blockquote",
    "hr",
]
_ALLOWED_ATTRS = {"a": ["href", "title"]}


def _harden_link(attrs: dict[Any, Any], new: bool = False) -> dict[Any, Any]:
    """Bleach linkify callback: harden every anchor's ``rel`` and ``target``.

    Applies to both author-written links (that survived the allowlist) and any
    bare URLs auto-linked by ``bleach.linkify``.
    """
    attrs[(None, "rel")] = "noopener nofollow noreferrer"
    attrs[(None, "target")] = "_blank"
    return attrs


def render_markdown(source: str) -> str:
    """Render Markdown to sanitized HTML.

    Args:
        source: Markdown source text. Empty/blank returns an empty string.

    Returns:
        Sanitized HTML: scripts, styles, ``onclick``, inline ``style=``, and any
        tag outside the allowlist are dropped; every link gets
        ``rel="noopener nofollow noreferrer"`` and ``target="_blank"``.
    """
    if not source:
        return ""
    raw = md.markdown(source, extensions=["extra", "sane_lists"])
    cleaned = bleach.clean(raw, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
    return bleach.linkify(cleaned, callbacks=[_harden_link], parse_email=False)
