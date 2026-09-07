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
  ``/static/help/…`` sources, heading ``id`` anchors on ``h2``-``h4``,
  internal links kept same-tab, and Confluence-style callouts via
  python-markdown's bundled ``admonition`` extension (``!!! tip`` etc. — the
  emitted ``div``/``p`` classes are allowlisted below, never a free ``class``
  attribute). Scripts, styles, and event handlers are stripped identically in
  both profiles.

This module is also the **dual-mode seam** for help/org page content
(:func:`render_page_content`): the /help/edit/ Quill editors save sanitized
HTML into the same columns that historically held Markdown, and a single sniff
— stored content whose ``lstrip()`` starts with ``<`` — routes HTML through
:func:`sanitize_page_html` while everything else renders through
:func:`render_markdown` exactly as before.
"""

from __future__ import annotations

import re
from typing import Any

import bleach
import markdown as md
from django.conf import settings
from django.utils.html import strip_tags
from django.utils.text import slugify

# Reused, not re-invented: the email rich-editor's Quill bullet-list normalizer and its
# HTML→text flattener (both battle-tested against Quill 2.x output). core.html_sanitize
# has no module-level project imports, so this cross-app import cannot cycle.
from core.html_sanitize import _normalize_quill_lists, rich_html_to_text

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

# Help profile: images, admonition ``div`` wrappers, and video ``iframe`` embeds
# join the allowlist (image/iframe sources and div/p classes restricted below).
_HELP_TAGS = [*_ALLOWED_TAGS, "img", "div", "iframe"]

# The member profile's exact extension list — unchanged, so member output stays
# byte-identical. The help profile adds the bundled ``admonition`` extension.
_MEMBER_EXTENSIONS = ["extra", "sane_lists", "tables"]
_HELP_EXTENSIONS = [*_MEMBER_EXTENSIONS, "admonition"]

# The only class values that survive on a help-profile ``div`` — exactly what
# the admonition extension emits for the callout types we style. Anything else
# (``!!! danger``, a hand-written ``class="pl-anything"``) loses the attribute.
_ADMONITION_DIV_CLASSES = frozenset({"admonition", "note", "info", "tip", "warning"})

# Help images may only come from the committed screenshot tree — no external
# URLs, no data: payloads, no protocol-relative hosts, no /media/ uploads.
_HELP_IMG_SRC_PREFIX = "/static/help/"

# Heading anchors carry help-registry keys; keep them boring and predictable.
_HEADING_ID_PATTERN = re.compile(r"^[a-z0-9-]{1,80}$")

# Help video embeds may only come from these two players — Loom (guide walkthroughs)
# and YouTube's privacy-enhanced host (no cookies until play). Never plain youtube.com,
# never an arbitrary host: an iframe is a full browsing context, so the src allowlist
# is the entire security story.
_HELP_IFRAME_SRC_PREFIXES = (
    "https://www.loom.com/embed/",
    "https://www.youtube-nocookie.com/embed/",
)

#: ``referrerpolicy`` values an author may set on a help-profile ``iframe``.
#: Embedded players reject playback when the site-wide ``Referrer-Policy:
#: same-origin`` leaves them unable to identify the embedding origin (YouTube
#: error 153), so the attribute has to survive sanitizing. Only origin-only
#: policies are allowed — they name the site without leaking the full URL.
_HELP_IFRAME_REFERRERPOLICIES = frozenset(
    {
        "origin",
        "strict-origin",
        "strict-origin-when-cross-origin",
    }
)

# An ``img`` whose ``src`` was rejected (or never present) is removed entirely in
# a follow-up pass — bleach only drops the attribute, leaving a useless tag.
_SRCLESS_IMG_RE = re.compile(r"<img\b(?![^>]*\bsrc=)[^>]*>")

# Same follow-up pass for an ``iframe`` whose ``src`` was rejected: bleach keeps the
# now-useless (and src-less, hence harmless) tag pair; strip it entirely.
_SRCLESS_IFRAME_RE = re.compile(r"<iframe\b(?![^>]*\bsrc=)[^>]*>\s*</iframe>")


def _allow_help_img_attr(tag: str, name: str, value: str) -> bool:
    """Bleach attribute filter for help-profile ``img``: src/alt/title only, local src."""
    if name in ("alt", "title"):
        return True
    return name == "src" and value.startswith(_HELP_IMG_SRC_PREFIX)


def _allow_help_iframe_attr(tag: str, name: str, value: str) -> bool:
    """Bleach attribute filter for help-profile ``iframe``: allowlisted video src only.

    ``src`` must start with a :data:`_HELP_IFRAME_SRC_PREFIXES` prefix (Loom or
    privacy-mode YouTube). ``title`` (accessibility), ``allowfullscreen``, and
    ``loading`` survive, as does ``referrerpolicy`` when set to one of
    :data:`_HELP_IFRAME_REFERRERPOLICIES` (without it the player cannot identify
    the embedding origin and refuses to play); everything else — ``srcdoc``,
    ``sandbox``, ``name``, event handlers — is dropped.
    """
    if name in ("title", "allowfullscreen", "loading"):
        return True
    if name == "referrerpolicy":
        return value in _HELP_IFRAME_REFERRERPOLICIES
    return name == "src" and value.startswith(_HELP_IFRAME_SRC_PREFIXES)


def _allow_help_heading_attr(tag: str, name: str, value: str) -> bool:
    """Bleach attribute filter for help-profile headings: pattern-valid ``id`` only."""
    return name == "id" and bool(_HEADING_ID_PATTERN.match(value))


def _allow_help_div_attr(tag: str, name: str, value: str) -> bool:
    """Bleach attribute filter for help-profile ``div``: admonition classes only.

    Every whitespace-separated token must come from the admonition allowlist —
    a smuggled class (``admonition evil``) drops the whole attribute, so a
    ``div`` can never carry an arbitrary class into the page.
    """
    if name != "class":
        return False
    tokens = value.split()
    return bool(tokens) and all(token in _ADMONITION_DIV_CLASSES for token in tokens)


def _allow_help_p_attr(tag: str, name: str, value: str) -> bool:
    """Bleach attribute filter for help-profile ``p``: the admonition title row only."""
    return name == "class" and value == "admonition-title"


_HELP_ATTRS = {
    "a": ["href", "title"],
    "th": ["align"],
    "td": ["align"],
    "img": _allow_help_img_attr,
    "iframe": _allow_help_iframe_attr,
    "h2": _allow_help_heading_attr,
    "h3": _allow_help_heading_attr,
    "h4": _allow_help_heading_attr,
    "div": _allow_help_div_attr,
    "p": _allow_help_p_attr,
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


# Wiki profile (member-authored wiki pages, brief §4 "Sanitizer"): the member tag set
# plus images, restricted to the wiki media prefix. No iframe, no div — an iframe is a
# full browsing context and stays the admin-authored help profile's privilege, and the
# wiki toolbar has no syntax to emit either one.
_WIKI_TAGS = [*_ALLOWED_TAGS, "img"]
_WIKI_EXTENSIONS = _MEMBER_EXTENSIONS  # no admonition — members have no syntax for it


def wiki_image_src_prefixes() -> tuple[str, ...]:
    """The ``src`` prefixes a wiki-profile ``img`` may use.

    Computed per call from settings, not a module constant, so ``override_settings``
    works in tests and dev/prod differ correctly: the local media prefix always
    counts, and R2's public prefix joins it once R2 is configured.
    """
    prefixes = (f"{settings.MEDIA_URL}wiki/",)
    r2_public_url = getattr(settings, "R2_PUBLIC_URL", "") or ""
    if r2_public_url:
        prefixes = (*prefixes, f"{r2_public_url}/wiki/")
    return prefixes


def _allow_wiki_img_attr(tag: str, name: str, value: str) -> bool:
    """Bleach attribute filter for wiki-profile ``img``: alt/title pass; src must be ours."""
    if name in ("alt", "title"):
        return True
    return name == "src" and value.startswith(wiki_image_src_prefixes())


_WIKI_ATTRS = {
    "a": ["href", "title"],
    "th": ["align"],
    "td": ["align"],
    "img": _allow_wiki_img_attr,
    "h2": _allow_help_heading_attr,
    "h3": _allow_help_heading_attr,
    "h4": _allow_help_heading_attr,
}


def render_markdown(source: str, *, profile: str = "member") -> str:
    """Render Markdown to sanitized HTML.

    Args:
        source: Markdown source text. Empty/blank returns an empty string.
        profile: ``"member"`` (default — today's exact member-content behavior),
            ``"help"`` (help-center articles: local images, heading anchors,
            same-tab internal links), or ``"wiki"`` (member wiki pages: member
            content plus images from the wiki media prefix, no iframe).

    Returns:
        Sanitized HTML: scripts, styles, ``onclick``, inline ``style=``, and any
        tag outside the allowlist are dropped; links are hardened per profile.

    Raises:
        ValueError: If ``profile`` is not a known profile name.
    """
    if profile not in ("member", "help", "wiki"):
        raise ValueError(f"Unknown markdown profile '{profile}'")
    if not source:
        return ""
    if profile == "member":
        raw = md.markdown(source, extensions=_MEMBER_EXTENSIONS)
        cleaned = bleach.clean(raw, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
        return bleach.linkify(cleaned, callbacks=[_harden_link], parse_email=False)
    if profile == "wiki":
        raw = md.markdown(source, extensions=_WIKI_EXTENSIONS)
        cleaned = bleach.clean(raw, tags=_WIKI_TAGS, attributes=_WIKI_ATTRS, strip=True)
        cleaned = _SRCLESS_IMG_RE.sub("", cleaned)
        return bleach.linkify(cleaned, callbacks=[_harden_link_help], parse_email=False)
    raw = md.markdown(source, extensions=_HELP_EXTENSIONS)
    cleaned = bleach.clean(raw, tags=_HELP_TAGS, attributes=_HELP_ATTRS, strip=True)
    cleaned = _SRCLESS_IMG_RE.sub("", cleaned)
    cleaned = _SRCLESS_IFRAME_RE.sub("", cleaned)
    return bleach.linkify(cleaned, callbacks=[_harden_link_help], parse_email=False)


# Page-content profile: exactly the /help/edit/ Quill toolbar's output and nothing more.
# No images (screenshots come from the committed /static/help/ pipeline, via Markdown),
# no headings past h3, no tables/code — the toolbar can't produce them, so the sanitizer
# doesn't allow them. Fail-closed: anything outside this list is stripped (inner text kept).
_PAGE_TAGS = [
    "p",
    "br",
    "h2",
    "h3",
    "strong",
    "em",
    "u",
    "s",
    "ol",
    "ul",
    "li",
    "a",
    "blockquote",
]
# ``href`` only — Quill's own target/rel are stripped and re-applied by the linkify
# callback below, and Quill classes (ql-*) never survive. No free attributes anywhere.
_PAGE_ATTRS = {"a": ["href"]}


def looks_like_html(source: str) -> bool:
    """The dual-mode sniff: stored content whose ``lstrip()`` starts with ``<`` is editor HTML.

    Markdown never opens with a raw ``<`` in practice (a line starting with ``<`` would be
    treated as inline HTML by the renderer anyway), and every Quill save starts with a block
    tag (``<p>``, ``<h2>``, …) — so one cheap check routes each stored value correctly.
    """
    return source.lstrip().startswith("<")


def sanitize_page_html(raw: str) -> str:
    """Sanitize /help/edit/ editor HTML to the page-content allowlist; harden links.

    Args:
        raw: The Quill editor's HTML. Treated as hostile input.

    Returns:
        Sanitized HTML — ``script``/``style``/``img``/``iframe``, event handlers, inline
        ``style=``, and every ``class`` (Quill's included) are dropped, tags outside
        :data:`_PAGE_TAGS` are stripped (inner text kept), Quill bullet lists become
        semantic ``<ul>``, and links are hardened like the help profile (internal links
        same-tab with ``rel="noopener"``, external fully hardened + new-tab). Empty,
        blank, or contentless input (an empty Quill editor is ``<p><br></p>``) returns
        ``""`` so blank-checks and the seed fill-if-blank contract keep working.
    """
    if not raw or not raw.strip():
        return ""
    normalized = _normalize_quill_lists(raw)
    cleaned = bleach.clean(normalized, tags=_PAGE_TAGS, attributes=_PAGE_ATTRS, strip=True)
    hardened = bleach.linkify(cleaned, callbacks=[_harden_link_help], parse_email=False)
    if not rich_html_to_text(hardened):
        return ""
    return hardened


def render_page_content(source: str, *, profile: str = "help") -> str:
    """Render a stored help/org page field — Markdown *or* rich-editor HTML — to safe HTML.

    The single decision point for the dual-mode columns (OrgInfoPage blocks, WikiArticle
    bodies, org FAQ answers): content that :func:`looks_like_html` goes through
    :func:`sanitize_page_html`; everything else renders through :func:`render_markdown`
    with ``profile``, byte-identical to the pre-editor behavior.

    Args:
        source: The stored field value. Empty/blank returns an empty string.
        profile: The Markdown profile used for non-HTML content (``"help"`` for page
            blocks and article bodies, ``"member"`` for org FAQ answers).

    Returns:
        Sanitized HTML, safe to ``mark_safe`` in a template.
    """
    if not source:
        return ""
    if looks_like_html(source):
        return sanitize_page_html(source)
    return render_markdown(source, profile=profile)


def sanitize_page_submission(value: str) -> str:
    """Form-save seam for a dual-mode field: sanitize editor HTML, pass Markdown through.

    A normal /help/edit/ save carries Quill HTML and gets sanitized before storage; a
    save whose value doesn't sniff as HTML (a no-JS fallback typing into the raw
    textarea, or an untouched Markdown value) is stored unchanged so it keeps rendering
    through the Markdown path.
    """
    if looks_like_html(value):
        return sanitize_page_html(value)
    return value


# --- Wiki page bodies — the member-writable /wiki/ store (dual-mode: Quill HTML or ------
# --- legacy Markdown, sniffed exactly like the help/org columns above). ----------------

# The Quill side of the wiki profile: the page-content tag set (what the shared toolbar
# already emits) plus what a member wiki page additionally needs — an image (the wiki
# toolbar's own image button), a fourth heading level, tables (a member pasting a spec
# sheet from a supplier's page should keep it), inline code, and a rule. Still no
# iframe, no div — those stay the admin-authored help profile's privilege.
_WIKI_TAGS_HTML = [*_PAGE_TAGS, "img", "h4", "table", "thead", "tbody", "tr", "th", "td", "code", "pre", "hr"]
_WIKI_ATTRS_HTML = {
    "a": ["href"],
    "img": _allow_wiki_img_attr,
    "th": ["align"],
    "td": ["align"],
}

# One pass over rendered wiki HTML: an h2/h3 with no id (or an invalid one) gets a
# slugified id computed from its own text, deduped -2/-3 within the same body.
_WIKI_HEADING_RE = re.compile(r"<(h[23])([^>]*)>(.*?)</\1>", re.DOTALL)
_HEADING_EXISTING_ID_RE = re.compile(r'\bid="([^"]*)"')


def sanitize_wiki_html(raw: str) -> str:
    """Sanitize a Quill wiki-page body: the page tag set plus images, no iframe/div.

    The Quill path for :func:`render_wiki_content` — bullet-list normalize, then
    ``bleach.clean`` with the wiki HTML allowlist, then drop any src-less ``img`` bleach
    left behind (a rejected external/`data:` source leaves a useless tag), then hardened
    auto-linking (internal links stay same-tab, matching the help profile). Empty,
    blank, or contentless input (an empty Quill editor is ``<p><br></p>``) returns ``""``.
    """
    if not raw or not raw.strip():
        return ""
    normalized = _normalize_quill_lists(raw)
    cleaned = bleach.clean(normalized, tags=_WIKI_TAGS_HTML, attributes=_WIKI_ATTRS_HTML, strip=True)
    cleaned = _SRCLESS_IMG_RE.sub("", cleaned)
    hardened = bleach.linkify(cleaned, callbacks=[_harden_link_help], parse_email=False)
    if not rich_html_to_text(hardened):
        return ""
    return hardened


def _inject_heading_ids(html: str) -> str:
    """Give every ``h2``/``h3`` in ``html`` a slugified ``id``, computed at render time.

    A member can rename a heading; a stored id would then point at text that no longer
    exists and the TOC chip would scroll nowhere. Computing the id here, in the same
    pass that produces the body HTML, makes the TOC and the anchors provably the same
    thing — see :func:`render_wiki_content`. An existing pattern-valid id is kept as-is;
    a missing or invalid one is replaced. Repeated heading text within one body is
    deduped ``-2``, ``-3``, ….
    """
    seen: dict[str, int] = {}

    def _unique(base: str) -> str:
        count = seen.get(base, 0)
        seen[base] = count + 1
        return base if count == 0 else f"{base}-{count + 1}"

    def _replace(match: re.Match[str]) -> str:
        tag, attrs, inner = match.group(1), match.group(2), match.group(3)
        existing = _HEADING_EXISTING_ID_RE.search(attrs)
        if existing is not None and _HEADING_ID_PATTERN.match(existing.group(1)):
            _unique(existing.group(1))  # reserve it, so a later duplicate text can't collide
            return match.group(0)
        anchor = _unique(slugify(strip_tags(inner)) or "section")
        return f'<{tag}{attrs} id="{anchor}">{inner}</{tag}>'

    return _WIKI_HEADING_RE.sub(_replace, html)


def render_wiki_content(source: str) -> str:
    """Render a stored :class:`~membership.models.WikiPage` body to safe, anchored HTML.

    The dual-mode entry point for wiki pages: content that :func:`looks_like_html` (a
    Quill save) goes through :func:`sanitize_wiki_html`; everything else renders through
    :func:`render_markdown` with ``profile="wiki"``. Either way, :func:`_inject_heading_ids`
    runs over the result last, so ``WikiPage.toc()`` — one regex pass over this same output
    — and the on-page anchors can never disagree, and running this twice on the same body
    yields the same ids (deterministic anchors).
    """
    if not source:
        return ""
    if looks_like_html(source):
        html = sanitize_wiki_html(source)
    else:
        html = render_markdown(source, profile="wiki")
    return _inject_heading_ids(html)


def sanitize_wiki_submission(value: str) -> str:
    """Form-save seam for a wiki page body: sanitize editor HTML, pass Markdown through.

    Mirrors :func:`sanitize_page_submission` exactly and does **not** call it — the two
    profiles must stay independently editable. A normal wiki save carries Quill HTML and
    gets sanitized before storage; a value that doesn't sniff as HTML (a no-JS fallback,
    or a legacy Markdown value) is stored unchanged so it keeps rendering through the
    Markdown path.
    """
    if looks_like_html(value):
        return sanitize_wiki_html(value)
    return value
