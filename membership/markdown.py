"""Render guild Markdown to sanitized, link-hardened HTML.

The single rendering path for member-authored Markdown across the app (guild
meeting-note bodies and the Help page how-it-works guides). ``render_markdown``
converts Markdown source to HTML, supports a full-but-safe tag set (headings
``h1``-``h6``, lists, tables, code, blockquotes), strips anything outside that
tight allowlist (scripts, styles, event handlers, inline ``style=``, unknown
tags), and hardens every surviving link with ``rel``/``target`` so the output is
safe to mark safe in a template.

Two profiles:

- ``member`` (default) — today's exact behavior, used for every member-authored
  surface. No images, every link hardened and opened in a new tab.
- ``help`` — the admin/repo-authored help-center profile: ``img`` restricted to
  ``/static/help/…`` sources, heading ``id`` anchors on ``h2``-``h4``, and
  internal links kept same-tab. Scripts, styles, and event handlers are stripped
  identically in both profiles.
"""

from __future__ import annotations

import re
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
    "h5",
    "h6",
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
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
]
# ``align`` is a safe, presentational attribute (the tables extension emits column
# alignment as inline ``style=`` instead, which we deliberately strip — so alignment
# only survives when an author hand-writes ``<td align="right">``). No ``style``, no
# event handlers: the sanitizer stays strict.
_ALLOWED_ATTRS = {"a": ["href", "title"], "th": ["align"], "td": ["align"]}

# Help profile: images join the allowlist (sources restricted below).
_HELP_TAGS = [*_ALLOWED_TAGS, "img"]

# Help images may only come from the committed screenshot tree — no external
# URLs, no data: payloads, no protocol-relative hosts, no /media/ uploads.
_HELP_IMG_SRC_PREFIX = "/static/help/"

# Heading anchors carry help-registry keys; keep them boring and predictable.
_HEADING_ID_PATTERN = re.compile(r"^[a-z0-9-]{1,80}$")

# An ``img`` whose ``src`` was rejected (or never present) is removed entirely in
# a follow-up pass — bleach only drops the attribute, leaving a useless tag.
_SRCLESS_IMG_RE = re.compile(r"<img\b(?![^>]*\bsrc=)[^>]*>")


def _allow_help_img_attr(tag: str, name: str, value: str) -> bool:
    """Bleach attribute filter for help-profile ``img``: src/alt/title only, local src."""
    if name in ("alt", "title"):
        return True
    return name == "src" and value.startswith(_HELP_IMG_SRC_PREFIX)


def _allow_help_heading_attr(tag: str, name: str, value: str) -> bool:
    """Bleach attribute filter for help-profile headings: pattern-valid ``id`` only."""
    return name == "id" and bool(_HEADING_ID_PATTERN.match(value))


_HELP_ATTRS = {
    "a": ["href", "title"],
    "th": ["align"],
    "td": ["align"],
    "img": _allow_help_img_attr,
    "h2": _allow_help_heading_attr,
    "h3": _allow_help_heading_attr,
    "h4": _allow_help_heading_attr,
}


def _harden_link(attrs: dict[Any, Any], new: bool = False) -> dict[Any, Any]:
    """Bleach linkify callback: harden every anchor's ``rel`` and ``target``.

    Applies to both author-written links (that survived the allowlist) and any
    bare URLs auto-linked by ``bleach.linkify``.
    """
    attrs[(None, "rel")] = "noopener nofollow noreferrer"
    attrs[(None, "target")] = "_blank"
    return attrs


def _harden_link_help(attrs: dict[Any, Any], new: bool = False) -> dict[Any, Any]:
    """Bleach linkify callback for the help profile.

    Internal links (href starting with ``/`` or ``#``) stay same-tab with
    ``rel="noopener"`` — help articles constantly deep-link into the app, and
    bouncing a member to a new tab for ``/guilds/voting/`` is hostile. External
    links keep the full member-profile hardening.
    """
    href = attrs.get((None, "href"), "")
    if href.startswith(("/", "#")):
        attrs[(None, "rel")] = "noopener"
        attrs.pop((None, "target"), None)
        return attrs
    return _harden_link(attrs, new)


def render_markdown(source: str, *, profile: str = "member") -> str:
    """Render Markdown to sanitized HTML.

    Args:
        source: Markdown source text. Empty/blank returns an empty string.
        profile: ``"member"`` (default — today's exact member-content behavior)
            or ``"help"`` (help-center articles: local images, heading anchors,
            same-tab internal links).

    Returns:
        Sanitized HTML: scripts, styles, ``onclick``, inline ``style=``, and any
        tag outside the allowlist are dropped; links are hardened per profile.

    Raises:
        ValueError: If ``profile`` is not a known profile name.
    """
    if profile not in ("member", "help"):
        raise ValueError(f"Unknown markdown profile '{profile}'")
    if not source:
        return ""
    raw = md.markdown(source, extensions=["extra", "sane_lists", "tables"])
    if profile == "member":
        cleaned = bleach.clean(raw, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
        return bleach.linkify(cleaned, callbacks=[_harden_link], parse_email=False)
    cleaned = bleach.clean(raw, tags=_HELP_TAGS, attributes=_HELP_ATTRS, strip=True)
    cleaned = _SRCLESS_IMG_RE.sub("", cleaned)
    return bleach.linkify(cleaned, callbacks=[_harden_link_help], parse_email=False)
