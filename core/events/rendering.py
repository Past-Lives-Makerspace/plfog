"""Constrained, SAFE merge-field rendering (design §2.3, Decision 6).

The copy team edits subject/body copy in the DB. Rendering that copy must **never**
execute raw Django (or any) template code from the database — that would be a
template-injection hole. Instead this module performs a *constrained substitution*:

* The only syntax recognised is ``{{ placeholder }}`` (optional surrounding
  whitespace). There are no tags, filters, expressions, attribute access, method
  calls, or control flow — nothing executable. A literal ``{% ... %}`` or
  ``{{ a.b }}`` is left untouched / flagged, not evaluated.
* Substitution draws ONLY from a whitelisted per-event context dict (the documented
  placeholders, :mod:`core.events.copy`). A placeholder not in the context is left
  as a **visible marker** ``[missing: name]`` rather than silently blanked — the
  copy team sees the gap in the live preview.
* For the HTML body, each substituted value is **autoescaped** (``django.utils.html.escape``)
  so a value containing ``<`` / ``&`` / quotes cannot inject markup. The text body is
  not escaped (it is plain text). The template literal itself is authored HTML and is
  trusted (only admins edit it); the *values* are the untrusted part and are escaped.

This is deliberately tiny and total: it cannot raise on bad input, cannot run code,
and degrades to a visible marker. :func:`validate_copy` is the forms-layer check that
flags unknown placeholders *at edit time* (so the copy team gets a clear error before
saving), separate from the render-time graceful degradation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

# Matches exactly ``{{ name }}`` where name is a bare identifier (letters, digits,
# underscores). The optional inner whitespace is tolerated. Anything else — dotted
# paths, filters, ``{% %}`` tags — does NOT match and is left in place untouched, so
# no executable construct is ever interpreted.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def placeholders_in(template: str) -> list[str]:
    """Every documented-style placeholder name appearing in ``template`` (in order).

    Only ``{{ name }}`` forms are reported; ``{% ... %}`` and ``{{ a.b }}`` are not
    placeholders here and are ignored.
    """
    return _PLACEHOLDER_RE.findall(template)


def render_text(template: str, context: Mapping[str, object]) -> str:
    """Render a plain-text template by constrained ``{{ placeholder }}`` substitution.

    Unknown placeholders (not in ``context``) become a visible ``[missing: name]``
    marker. Values are inserted as-is (plain text — no escaping). Never raises.
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in context:
            return str(context[name])
        return f"[missing: {name}]"

    return _PLACEHOLDER_RE.sub(_sub, template)


def render_html(template: str, context: Mapping[str, object]) -> SafeString:
    """Render an HTML template by constrained substitution with value autoescaping.

    The authored template literal is trusted markup (admin-edited); each substituted
    *value* is HTML-escaped so a value containing ``<script>`` or ``&`` cannot inject
    markup. Unknown placeholders become an escaped ``[missing: name]`` marker. Returns
    a :class:`~django.utils.safestring.SafeString` (the surrounding template is the
    intended HTML; only the interpolated values were escaped). Never raises.

    A value that is already a :class:`~django.utils.safestring.SafeString` is inserted
    **unescaped** — the opt-in Django convention for trusted, app-built markup (e.g. the
    voting results bar chart :meth:`FundingSnapshot.allocation_chart_html`). This never
    weakens the injection guard for DB copy: only *application code* can mark a context
    value safe; a value drawn from the DB or user input is a plain ``str`` and is still
    escaped. Any dynamic text inside a SafeString value must be escaped by whoever built
    it (the chart renders through Django's autoescaping template engine, which does).
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in context:
            value = context[name]
            if isinstance(value, SafeString):
                return str(value)  # trusted app-built markup — pass through unescaped
            return escape(str(value))
        return escape(f"[missing: {name}]")

    return mark_safe(_PLACEHOLDER_RE.sub(_sub, template))  # noqa: S308 - values escaped above; literal is admin-authored


@dataclass(frozen=True)
class RenderedCopy:
    """The fully-rendered copy for one (event, channel) against a context."""

    subject: str
    body_text: str
    body_html: str


def render_copy(
    *,
    subject: str,
    body_text: str,
    body_html: str,
    context: Mapping[str, object],
) -> RenderedCopy:
    """Render subject + both bodies against ``context`` with the safe substitution.

    Subject and text body use :func:`render_text`; the HTML body uses
    :func:`render_html` (value autoescaping). Used by both the live preview and the
    new :func:`core.events.emit.emit` send path.
    """
    return RenderedCopy(
        subject=render_text(subject, context),
        body_text=render_text(body_text, context),
        body_html=str(render_html(body_html, context)) if body_html else "",
    )


def unknown_placeholders(template: str, allowed: tuple[str, ...]) -> list[str]:
    """Placeholder names in ``template`` that are NOT in the documented ``allowed`` set.

    De-duplicated, in first-seen order. Used by :func:`validate_copy` to flag copy
    that references a merge field the event does not provide.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in placeholders_in(template):
        if name not in allowed and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def validate_copy(
    *,
    subject: str,
    body_text: str,
    body_html: str,
    allowed: tuple[str, ...],
) -> list[str]:
    """Return every undocumented placeholder used across the three copy fields.

    The edit form calls this so the copy team gets a clear validation error *before*
    saving copy that references a merge field the event does not expose (which would
    otherwise render as a ``[missing: …]`` marker at send time). An empty list means
    the copy only uses documented placeholders.
    """
    bad: list[str] = []
    seen: set[str] = set()
    for fragment in (subject, body_text, body_html):
        for name in unknown_placeholders(fragment, allowed):
            if name not in seen:
                seen.add(name)
                bad.append(name)
    return bad
