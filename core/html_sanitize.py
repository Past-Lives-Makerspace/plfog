"""Sanitize and render admin-authored rich-text email bodies.

The single rendering path for any HTML an admin/instructor types into the Quill
rich-text editor (sitewide announcement, class welcome email, guild orientation /
welcome emails). Editor HTML is treated as **hostile**:

* :func:`sanitize_rich_html` strips everything outside a small allowlist, normalizes
  Quill's bullet-list quirk to a semantic ``<ul>``, and hardens every surviving link.
* :func:`render_rich_email_body` turns a *stored* body — rich HTML, or legacy plain
  text from before the editor existed — into inline-styled, email-ready HTML for the
  dark ``#092E4C`` card.
* :func:`render_rich_email_text` produces the plain-text ``.txt`` counterpart.
* :func:`rich_html_to_text` flattens HTML to one readable line for the in-app bell and
  Discord fallback.
* :func:`clean_rich_body`, :func:`render_rich_body` and :func:`rich_body_to_text` are the
  same three steps for a body that renders on a page rather than in an email (the class
  description): what a form stores, what the page shows, and the plain text for a feed.

Modeled on :mod:`membership.markdown` (same ``bleach`` + ``_harden_link`` posture).
"""

from __future__ import annotations

import html as html_module
import re
from typing import Any

import bleach

# Only these tags survive sanitization — the editor's seven controls and nothing more.
# Everything else is stripped (inner text kept). No ``h1``: that is the email shell's
# own title, so author subheadings start at ``h2``.
_ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "h2",
    "h3",
    "ul",
    "ol",
    "li",
    "a",
    "blockquote",
]
_ALLOWED_ATTRS = {"a": ["href", "title"]}

# A stored body containing any of these tags is editor HTML; otherwise it is legacy
# plain text (pre-editor saves) and gets escaped + paragraph-ized on render.
_BLOCK_TAG_RE = re.compile(r"<(?:p|br|h2|h3|ul|ol|li|blockquote)\b", re.IGNORECASE)
# A close tag (or ``<br>``) that marks a line break when flattening HTML to text.
_LINE_BREAK_RE = re.compile(r"<br\s*/?>|</(?:p|li|h2|h3|blockquote|ul|ol)>", re.IGNORECASE)
# Quill 2.x emits a bullet list as ``<ol>`` whose items carry ``data-list="bullet"``.
_OL_BLOCK_RE = re.compile(r"<ol(\s[^>]*)?>(.*?)</ol>", re.IGNORECASE | re.DOTALL)
_BULLET_ITEM_RE = re.compile(r"""data-list\s*=\s*["']bullet["']""", re.IGNORECASE)
# A script or style element, contents and all. ``bleach`` strips the tags but keeps what
# is between them, so CSS or code pasted in would otherwise survive as visible text.
_RAW_TEXT_ELEMENT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)


def _harden_link(attrs: dict[Any, Any], new: bool = False) -> dict[Any, Any]:
    """Bleach linkify callback: harden every anchor's ``rel`` and ``target``."""
    attrs[(None, "rel")] = "noopener nofollow noreferrer"
    attrs[(None, "target")] = "_blank"
    return attrs


def _normalize_quill_lists(raw: str) -> str:
    """Rewrite Quill bullet lists (``<ol>`` with ``data-list="bullet"`` items) to ``<ul>``.

    Runs before sanitization, while the ``data-list`` marker still exists — ``bleach``
    drops the attribute moments later. Quill flattens nesting into indent classes, so
    each list is a single non-nested ``<ol>``; a list whose items are bullets becomes a
    real ``<ul>``, an ordered list stays ``<ol>``.
    """

    def repl(match: re.Match[str]) -> str:
        attrs, inner = match.group(1) or "", match.group(2)
        if _BULLET_ITEM_RE.search(inner):
            return f"<ul>{inner}</ul>"
        return f"<ol{attrs}>{inner}</ol>"

    return _OL_BLOCK_RE.sub(repl, raw)


def rich_html_to_text(html: str) -> str:
    """Flatten sanitized HTML to one readable line (in-app bell + Discord fallback).

    Tags become plain text, line-breaking tags become spaces, entities are unescaped,
    and runs of whitespace collapse to single spaces.
    """
    if not html:
        return ""
    spaced = _LINE_BREAK_RE.sub(" ", html)
    stripped = bleach.clean(spaced, tags=[], attributes={}, strip=True)
    text = html_module.unescape(stripped)
    return re.sub(r"\s+", " ", text).strip()


def sanitize_rich_html(raw: str) -> str:
    """Sanitize editor HTML to the allowlist; normalize Quill lists; harden links.

    Args:
        raw: The editor's HTML. Treated as hostile input.

    Returns:
        Sanitized HTML — ``script`` and ``style`` elements are dropped with their contents;
        ``iframe``/event handlers/inline ``style=``/``class=`` and any other tag outside the
        allowlist are dropped (inner text kept); every link gets
        ``rel="noopener nofollow noreferrer" target="_blank"``; Quill bullet lists become semantic ``<ul>``. Empty, blank, or contentless input
        (an empty Quill editor is ``<p><br></p>``) returns ``""``.
    """
    if not raw or not raw.strip():
        return ""
    normalized = _normalize_quill_lists(_RAW_TEXT_ELEMENT_RE.sub("", raw))
    cleaned = bleach.clean(normalized, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
    hardened = bleach.linkify(cleaned, callbacks=[_harden_link], parse_email=False)
    if not rich_html_to_text(hardened):
        return ""
    return hardened


def is_editor_html(value: str) -> bool:
    """True when a stored body is editor HTML (it carries a block tag), false for plain text.

    Plain text is what every body held before its editor existed, and what a client without
    the editor still posts. The two render differently: HTML is sanitized, text is escaped and
    paragraph-ized, so a typed ``<safety glasses>`` in plain text is shown, not swallowed.
    """
    return bool(_BLOCK_TAG_RE.search(value or ""))


def clean_rich_body(raw: str) -> str:
    """What a form stores for a body that may arrive as editor HTML or as plain text.

    Editor HTML is sanitized to the allowlist; an empty editor (``<p><br></p>``) stores ``""``.
    Plain text is kept exactly as typed, angle brackets and all: the renderer escapes it, and
    running it through the sanitizer would read the brackets as a tag and drop the words between
    them (issue #425).
    """
    if not raw or not raw.strip():
        return ""
    if is_editor_html(raw):
        return sanitize_rich_html(raw)
    return raw


def render_rich_body(value: str) -> str:
    """Page-ready HTML for a stored body: editor HTML sanitized, plain text escaped and paragraph-ized.

    The on-page counterpart of :func:`render_rich_email_body`, without the inline styles: the
    page's own stylesheet styles the fragment. Returns ``""`` for empty input; the result is safe
    to ``mark_safe``.
    """
    if not value or not value.strip():
        return ""
    if is_editor_html(value):
        return sanitize_rich_html(value)
    return _legacy_plaintext_to_html(value)


def rich_body_to_text(value: str) -> str:
    """Multi-line plain text of a stored body, for anywhere HTML cannot go (a calendar feed, a meta tag).

    Plain text is returned unchanged; editor HTML is sanitized then flattened, list items as
    ``- `` bullets. Returns ``""`` for empty input.
    """
    return render_rich_email_text(value)


def _legacy_plaintext_to_html(value: str) -> str:
    """Escape + paragraph-ize legacy plain text (blank line → ``<p>``, newline → ``<br>``)."""
    from django.utils.html import escape

    return "".join(
        f"<p>{escape(chunk.strip()).replace(chr(10), '<br>')}</p>" for chunk in value.split("\n\n") if chunk.strip()
    )


def render_rich_email_body(value: str) -> str:
    """Email-ready, inline-styled HTML for a stored body — rich HTML *or* legacy text.

    A value that already contains a block tag is editor HTML → sanitized; otherwise it
    is legacy plain text → escaped and paragraph-ized. Either way the fragment is then
    inline-styled for the dark email card. Returns ``""`` for empty input. The result is
    safe to ``mark_safe`` (the only HTML in it is the sanitizer's own allowlisted tags
    plus the inline styles).
    """
    if not value or not value.strip():
        return ""
    from core.events.templates import style_rich_email_fragment

    fragment = render_rich_body(value)
    if not fragment:
        return ""
    return style_rich_email_fragment(fragment)


def render_rich_email_text(value: str) -> str:
    """Plain-text rendering of a stored body for the ``.txt`` email part.

    Legacy plain text is returned unchanged (its line breaks are already meaningful);
    editor HTML is sanitized then flattened to readable multi-line text (list items
    become ``- `` bullets). Returns ``""`` for empty input.
    """
    if not value or not value.strip():
        return ""
    if not is_editor_html(value):
        return value
    return _html_to_multiline_text(sanitize_rich_html(value))


def _html_to_multiline_text(html: str) -> str:
    """Flatten sanitized HTML to multi-line plain text, preserving paragraphs and bullets."""
    if not html:
        return ""
    text = re.sub(r"<li(?:\s[^>]*)?>", "\n- ", html, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|h2|h3|blockquote|ul|ol|li)>", "\n", text, flags=re.IGNORECASE)
    stripped = bleach.clean(text, tags=[], attributes={}, strip=True)
    unescaped = html_module.unescape(stripped)
    collapsed = re.sub(r"[ \t]+", " ", unescaped)
    collapsed = re.sub(r" *\n *", "\n", collapsed)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
    return collapsed.strip()
