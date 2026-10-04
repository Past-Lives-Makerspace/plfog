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
* :func:`limit_rich_text` is the size limit every form and autosave applies before it
  sanitizes what a member typed.

Cleaning is :class:`AllowlistCleaner` (``nh3``, the Rust ``ammonia`` sanitizer; it slows only
on list nesting thousands deep, under a second at the size limit); auto-linking and link
hardening is :func:`core.linkify.linkify`. :mod:`membership.markdown` shares both.
"""

from __future__ import annotations

import html as html_module
import re
from collections.abc import Callable, Iterable, Mapping

import nh3
from django.core.exceptions import ValidationError

from core.linkify import linkify

#: The longest rich text a form or autosave accepts. Production's longest stored body is
#: under 8,000 characters, so this only ever stops input nobody typed.
RICH_TEXT_MAX_CHARS = 100_000
RICH_TEXT_TOO_LONG = f"This is too long to save. Shorten it to under {RICH_TEXT_MAX_CHARS:,} characters."

#: Link schemes that survive sanitizing; anything else loses its ``href``/``src``. Relative
#: links and ``#anchors`` always survive.
URL_SCHEMES = frozenset({"http", "https", "mailto", "tel"})

#: An attribute filter: ``(tag, name, value)`` → keep the attribute?
AttrFilter = Callable[[str, str, str], bool]

# Block-level elements (bleach's list, from MDN). Stripping the start tag of one leaves a line
# break, as a browser would show it, so "<div>1</div><div>Class</div>" reads "1 Class", not
# "1Class".
_BLOCK_LEVEL_TAGS = frozenset(
    """address article aside blockquote details dialog dd div dl dt fieldset figcaption figure
    footer form h1 h2 h3 h4 h5 h6 header hgroup hr li main nav ol p pre section table ul""".split()
)
# One piece of markup as an HTML tokenizer reads it: a comment; a bogus comment ("<!x>", "<?x>",
# "</ x>"); a start or end tag (end slash in group 1, name in group 2, the closing ">" in group 3,
# quoted attribute values read whole so a ">" inside one does not end the tag); or a "<" that
# begins none of these. Every alternative is linear, and the tag form always matches (an
# unclosed tag runs to the end of the input), so a failed attempt never rescans.
_MARKUP_RE = re.compile(
    r"<!--->|<!-->|<!--.*?(?:--!?>|\Z)"
    r"|<[!?][^>]*>?|</(?![A-Za-z])[^>]*>?"
    r"""|<(/?)([A-Za-z][^\s/>]*)(?:[^>=]|=\s*"[^"]*"|=\s*'[^']*'|=)*(>?)"""
    r"|<",
    re.DOTALL,
)


def _strip_disallowed_tags(html: str, allowed: frozenset[str], bare: frozenset[str]) -> str:
    """Remove every tag outside ``allowed`` from the markup before it is parsed.

    This is how bleach stripped, and keeping it keeps output the same: a tag that is not
    allowed never reaches the HTML parser, so it cannot close an open paragraph or turn what
    follows into raw text (a ``<style>``'s contents are read as markup and their tags stripped
    too). A block-level start tag becomes a newline once any tag has been read. A ``<`` that
    starts no tag is text, and so is a tag cut off by the end of the input, as bleach kept it.
    Comments are left for the sanitizer, which drops them.

    A tag in ``bare`` (allowed, but with no attribute it may keep) loses its attributes here,
    before parsing rather than after: the output is the same and the parser does less work.
    """
    seen_tag = False

    def replace(match: re.Match[str]) -> str:
        nonlocal seen_tag
        token, name = match.group(0), match.group(2)
        if name is None:
            return "&lt;" if token == "<" else token
        if not match.group(3):
            # Cut off by the end of the input ("tag <stro"): what was typed stays, as text.
            return token.replace("<", "&lt;")
        follows_tag, seen_tag = seen_tag, True
        tag = name.lower()
        if tag in bare:
            return f"</{tag}>" if match.group(1) else f"<{tag}>"
        if tag in allowed:
            return token
        if follows_tag and not match.group(1) and tag in _BLOCK_LEVEL_TAGS:
            return "\n"
        return ""

    return _MARKUP_RE.sub(replace, html)


class AllowlistCleaner:
    """Sanitize HTML to a tag and attribute allowlist, the way ``bleach.clean(strip=True)`` did.

    Tags outside ``tags`` go and their text stays (a script's too, as inert text); comments go;
    an attribute survives only if ``attributes`` names it for its tag and that tag's ``filters``
    entry, if any, accepts it; a URL survives only with a scheme in :data:`URL_SCHEMES` or none.
    ``rel`` and ``target`` are left to the link hardener (:func:`core.linkify.linkify`).
    """

    def __init__(
        self,
        tags: Iterable[str],
        attributes: Mapping[str, set[str]],
        filters: Mapping[str, AttrFilter] | None = None,
    ) -> None:
        self.tags = frozenset(tags)
        self._bare = frozenset(tag for tag in self.tags if not attributes.get(tag))
        rules = dict(filters or {})

        def attribute_filter(tag: str, name: str, value: str) -> str | None:
            if tag not in rules or rules[tag](tag, name, value):
                return value
            return None

        self._nh3 = nh3.Cleaner(
            tags=set(self.tags),
            clean_content_tags=set(),
            attributes={"*": set(), **attributes},
            attribute_filter=attribute_filter,
            link_rel=None,
            url_schemes=set(URL_SCHEMES),
        )

    def clean(self, html: str) -> str:
        """The sanitized HTML: well formed, ``<`` in text escaped, attribute values double quoted."""
        return self._nh3.clean(_strip_disallowed_tags(html, self.tags, self._bare))


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
_ALLOWED_ATTRS = {"a": {"href", "title"}}

# Tag-free text: every tag goes, text (script and style text included) stays, escaped.
_TEXT_ONLY = AllowlistCleaner((), {})
_RICH_CLEANER = AllowlistCleaner(_ALLOWED_TAGS, _ALLOWED_ATTRS)

# A stored body containing any of these tags is editor HTML; otherwise it is legacy
# plain text (pre-editor saves) and gets escaped + paragraph-ized on render.
_BLOCK_TAG_RE = re.compile(r"<(?:p|br|h2|h3|ul|ol|li|blockquote)\b", re.IGNORECASE)
# A close tag (or ``<br>``) that marks a line break when flattening HTML to text.
_LINE_BREAK_RE = re.compile(r"<br\s*/?>|</(?:p|li|h2|h3|blockquote|ul|ol)>", re.IGNORECASE)
# Quill 2.x emits a bullet list as ``<ol>`` whose items carry ``data-list="bullet"``. A list
# with no end tag runs to the end of the input, which keeps this scan linear: an opener never
# rescans the rest for a closer it lacks.
_OL_BLOCK_RE = re.compile(r"<ol(\s[^>]*)?(?:>(.*?)(?:</ol>|\Z)|\Z)", re.IGNORECASE | re.DOTALL)
_BULLET_ITEM_RE = re.compile(r"""data-list\s*=\s*["']bullet["']""", re.IGNORECASE)
# A script or style element, contents and all. Stripping keeps the text between stripped
# tags, so CSS or code pasted in would otherwise survive as visible text. An element with no
# end tag runs to the end of the input, as it does in a browser; that also keeps the scan
# linear, since an opener never rescans the rest for a closer it lacks.
_RAW_TEXT_ELEMENT_RE = re.compile(r"<(script|style)\b[^>]*(?:>.*?(?:</\1\s*>|\Z)|\Z)", re.IGNORECASE | re.DOTALL)


def _harden_link(attrs: dict[str, str]) -> dict[str, str]:
    """Link hardener: every anchor opens in a new tab with ``rel="noopener nofollow noreferrer"``."""
    attrs["rel"] = "noopener nofollow noreferrer"
    attrs["target"] = "_blank"
    return attrs


class RichTextTooLongError(ValidationError, ValueError):
    """Rich text over :data:`RICH_TEXT_MAX_CHARS`, raised by :func:`limit_rich_text`.

    A ``ValidationError``, so a form shows it under the field, and a ``ValueError`` whose
    ``str()`` is the copy, so the autosave views (which toast ``str(exc)`` of a ``ValueError``)
    show the same words.
    """

    def __init__(self) -> None:
        super().__init__(RICH_TEXT_TOO_LONG, code="too_long")

    def __str__(self) -> str:
        return RICH_TEXT_TOO_LONG


def limit_rich_text(value: str) -> str:
    """Return ``value`` unchanged, or raise :class:`RichTextTooLongError` when it is too long.

    Every path that saves what a member typed into a rich text or Markdown field calls this
    before sanitizing; renderers of stored data do not.

    Raises:
        RichTextTooLongError: ``value`` is longer than :data:`RICH_TEXT_MAX_CHARS`.
    """
    if len(value) > RICH_TEXT_MAX_CHARS:
        raise RichTextTooLongError()
    return value


def _normalize_quill_lists(raw: str) -> str:
    """Rewrite Quill bullet lists (``<ol>`` with ``data-list="bullet"`` items) to ``<ul>``.

    Runs before sanitization, while the ``data-list`` marker still exists — the sanitizer
    drops the attribute moments later. Quill flattens nesting into indent classes, so
    each list is a single non-nested ``<ol>``; a list whose items are bullets becomes a
    real ``<ul>``, an ordered list stays ``<ol>``.
    """

    def repl(match: re.Match[str]) -> str:
        attrs, inner = match.group(1) or "", match.group(2) or ""
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
    text = html_module.unescape(_TEXT_ONLY.clean(spaced))
    return re.sub(r"\s+", " ", text).strip()


def sanitize_rich_html(raw: str) -> str:
    """Sanitize editor HTML to the allowlist; normalize Quill lists; harden links.

    Args:
        raw: The editor's HTML. Treated as hostile input.

    Returns:
        Sanitized HTML — ``script`` and ``style`` elements are dropped with their contents;
        ``iframe``/event handlers/inline ``style=``/``class=`` and any other tag outside the
        allowlist are dropped (inner text kept); every link gets
        ``rel="noopener nofollow noreferrer" target="_blank"``; Quill bullet lists become
        semantic ``<ul>``. Empty, blank, or contentless input (an empty Quill editor is
        ``<p><br></p>``) returns ``""``.
    """
    if not raw or not raw.strip():
        return ""
    normalized = _normalize_quill_lists(_RAW_TEXT_ELEMENT_RE.sub("", raw))
    hardened = linkify(_RICH_CLEANER.clean(normalized), _harden_link)
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


def clean_rich_html(raw: str) -> str:
    """What a form or autosave stores for editor HTML: the size limit, then :func:`sanitize_rich_html`.

    Raises:
        RichTextTooLongError: ``raw`` is longer than :data:`RICH_TEXT_MAX_CHARS`.
    """
    return sanitize_rich_html(limit_rich_text(raw))


def clean_rich_body(raw: str) -> str:
    """What a form stores for a body that may arrive as editor HTML or as plain text.

    Editor HTML is sanitized to the allowlist; an empty editor (``<p><br></p>``) stores ``""``.
    Plain text is kept exactly as typed, angle brackets and all: the renderer escapes it, and
    running it through the sanitizer would read the brackets as a tag and drop the words between
    them (issue #425). Either way the size limit applies first.

    Raises:
        RichTextTooLongError: ``raw`` is longer than :data:`RICH_TEXT_MAX_CHARS`.
    """
    if not raw or not raw.strip():
        return ""
    limit_rich_text(raw)
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
    unescaped = html_module.unescape(_TEXT_ONLY.clean(text))
    collapsed = re.sub(r"[ \t]+", " ", unescaped)
    collapsed = re.sub(r" *\n *", "\n", collapsed)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
    return collapsed.strip()
