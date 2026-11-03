"""Repo-wide template lint: a new-tab link to a standalone page carries ``data-pl-new-window``.

Inside the native app ``static/js/native-new-tab-links.js`` opens a same-host
``target="_blank"`` link in place, because the iOS shell hands a new-window navigation to
Safari, which has no session (#1102). That is right for a page inside the hub, which has the
sidebar and top bar to leave by. A flyer, QR or sticker sheet, or print page is a document of
its own with no navigation, and iOS has no back swipe, so opened in place it strands the member;
``data-pl-new-window`` tells the script to leave such a link to the shell (FRONTEND.md rule 24).

The rule follows each ``target="_blank"`` link to where it goes: a ``{% url %}`` in the tag, a
``{% url ... as var %}`` in the same template, or an ``{% include ... with param=var %}`` one
level down. The URL name resolves to its view, the view's source names the templates it renders,
and a page has the hub navigation when its ``{% extends %}`` chain reaches ``hub/base.html``. A
link to a page without it must carry the mark; a link to a page with it must not, so the mark
keeps meaning one thing. A view whose template is not named in its own source (a shared render
helper, a redirect) is registered in ``RENDERED_ELSEWHERE`` and the spec fails until it is. A URL
built in Python reaches the template as a plain variable, so a backstop also holds any
``target="_blank"`` link whose variable is named for a flyer, sticker sheet, print or
reconciliation page (``item.flyer_url``) to the mark.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from pathlib import Path

from django.urls import URLPattern, URLResolver, get_resolver

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
CHROME_BASE = "hub/base.html"
MARK = "data-pl-new-window"

#: URL name -> the template its view renders, when the view's own source does not name it, or
#: None when the view only redirects (where it lands is the hub or another host, never a dead end).
RENDERED_ELSEWHERE: dict[str, str | None] = {
    # Both preview views hand off to _render_class_preview.
    "classes:class_preview": "classes/public/detail.html",
    "classes:class_review_preview": "classes/public/detail.html",
    # Redirects to Stripe's Express dashboard (another host, which the shell gives to the system
    # browser) or back to the hub's payouts tab.
    "billing_payouts_dashboard": None,
    # hub/guild_detail.html extends {{ guilds_page_base }} (core/context_processors.py): the guilds
    # host's guilds/base_public.html or hub/base.html, and the first extends the second.
    "hub_guild_detail": "guilds/base_public.html",
}

URL_REF = re.compile(r"{%\s*url\s+['\"]([^'\"]+)['\"]")
URL_AS = re.compile(r"{%\s*url\s+['\"]([^'\"]+)['\"][^%]*?\bas\s+(\w+)\s*%}")
INCLUDE = re.compile(r"{%\s*include\s+['\"]([^'\"]+)['\"]([^%]*)%}")
EXTENDS = re.compile(r"{%\s*extends\s+(['\"]?)([^'\"\s%]+)\1\s*%}")
TEMPLATE_LITERAL = re.compile(r"['\"]([\w/\-]+\.html)['\"]")
BLANK = re.compile(r"""\btarget\s*=\s*["']_blank["']""")
STANDALONE_VAR = re.compile(r"{{\s*[\w.]*(?:flyer|sticker|print|reconciliation)\w*_url\b")


def _views() -> dict[str, Callable[..., object]]:
    """Every named URL (with its namespace prefix) mapped to its view callable."""
    found: dict[str, Callable[..., object]] = {}

    def walk(resolver: URLResolver, prefix: str) -> None:
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern, prefix + (f"{pattern.namespace}:" if pattern.namespace else ""))
            elif isinstance(pattern, URLPattern) and pattern.name:
                found.setdefault(prefix + pattern.name, pattern.callback)

    walk(get_resolver(), "")
    return found


def _chain(template: str, templates: dict[str, str]) -> list[str]:
    """``template`` and every base it extends, in order; ``"?"`` ends a chain it cannot follow."""
    chain = [template]
    while chain[-1] in templates:
        match = EXTENDS.search(templates[chain[-1]])
        if not match:
            return chain
        chain.append(match.group(2) if match.group(1) else "?")
    return [*chain, "?"] if chain[-1] != "?" else chain


def _has_chrome(name: str, views: dict[str, Callable[..., object]], templates: dict[str, str]) -> bool | None:
    """True when the page at URL ``name`` sits inside the hub, False when it stands alone,
    None when the spec cannot tell (an unregistered view, an extends it cannot follow)."""
    if name in RENDERED_ELSEWHERE:
        rendered = RENDERED_ELSEWHERE[name]
        if rendered is None:
            return True
        pages = [rendered]
    else:
        view = views.get(name)
        if view is None:
            return None
        source = inspect.getsource(inspect.unwrap(getattr(view, "view_class", view)))
        pages = TEMPLATE_LITERAL.findall(source)
        if not pages:
            return None
    chains = [_chain(page, templates) for page in pages]
    if any(chain[-1] == "?" and CHROME_BASE not in chain for chain in chains):
        return None
    return all(CHROME_BASE in chain for chain in chains)


def _anchors(text: str) -> list[tuple[int, str]]:
    """Every ``<a …>`` opening tag with its 1-based line, quote-aware (an Alpine ``=>`` is not the end)."""
    tags = []
    for match in re.finditer(r"<a(?=[\s>])", text, re.IGNORECASE):
        i = match.end()
        quote = None
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == ">":
                break
            i += 1
        tags.append((text.count("\n", 0, match.start()) + 1, text[match.start() : i + 1]))
    return tags


def _uses(var: str, tag: str) -> bool:
    return re.search(r"{{\s*" + re.escape(var) + r"\b", tag) is not None


def _verdict(
    tag: str, names: set[str], views: dict[str, Callable[..., object]], templates: dict[str, str]
) -> str | None:
    """Why a ``target="_blank"`` tag pointing at ``names`` breaks the rule, or None when it is fine."""
    marked = MARK in tag
    for name in sorted(names):
        chrome = _has_chrome(name, views, templates)
        if chrome is None:
            return f"{name}: cannot tell what page it renders; register it in RENDERED_ELSEWHERE"
        if not chrome and not marked:
            return f"{name}: a page without the hub navigation, missing {MARK}"
        if chrome and marked:
            return f"{name}: a page inside the hub, so {MARK} only strands the link in Safari"
    if not names and STANDALONE_VAR.search(tag) and not marked:
        return f"a standalone page's URL, missing {MARK}"
    return None


def _offenders_in(
    text: str, template: str, templates: dict[str, str], views: dict[str, Callable[..., object]]
) -> list[str]:
    assigned: dict[str, set[str]] = {}
    for name, var in URL_AS.findall(text):
        assigned.setdefault(var, set()).add(name)
    offenders = []
    for lineno, tag in _anchors(text):
        if not BLANK.search(tag):
            continue
        names = set(URL_REF.findall(tag)) | {n for var, ns in assigned.items() if _uses(var, tag) for n in ns}
        if reason := _verdict(tag, names, views, templates):
            offenders.append(f"{template}:{lineno} ({reason})")
    # One level of include: a parameter fed from a {% url as %} lands on a tag in the component.
    for included, args in INCLUDE.findall(text):
        for param, var in re.findall(r"(\w+)=(\w+)", args):
            if var not in assigned or included not in templates:
                continue
            for lineno, tag in _anchors(templates[included]):
                if BLANK.search(tag) and _uses(param, tag):
                    if reason := _verdict(tag, assigned[var], views, templates):
                        offenders.append(f"{included}:{lineno} (via {template} {param}={var}; {reason})")
    return offenders


def _all_templates() -> dict[str, str]:
    return {
        path.relative_to(TEMPLATES_DIR).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(TEMPLATES_DIR.rglob("*.html"))
    }


def describe_new_window_links():
    def it_registers_only_routes_that_exist():
        views = _views()
        assert [name for name in RENDERED_ELSEWHERE if name not in views] == []

    def it_marks_every_new_tab_link_to_a_standalone_page_and_no_other():
        templates = _all_templates()
        views = _views()
        offenders = sorted({o for name, text in templates.items() for o in _offenders_in(text, name, templates, views)})
        assert not offenders, (
            f'A same-host target="_blank" link to a page without the hub navigation carries {MARK}, '
            "or the native app opens it in place with no way back (FRONTEND.md rule 24):\n  " + "\n  ".join(offenders)
        )

    def it_actually_detects_an_unmarked_standalone_link_and_a_needless_mark():
        # Self-test so a refactor can't quietly neuter the lint.
        templates = {
            "hub/base.html": "<html></html>",
            "inside.html": '{% extends "hub/base.html" %}',
            "alone.html": "<html>flyer</html>",
            "shell.html": "{% extends screen_shell %}",
        }

        def inside():
            return "inside.html"

        def alone():
            return "alone.html"

        def shell():
            return "shell.html"

        def redirect_only():
            return None

        views = {"in": inside, "out": alone, "shelled": shell, "bare": redirect_only}
        leaky = "\n".join(
            [
                '<a href="{% url \'out\' %}" target="_blank">Flyer</a>',
                f'<a href="{{% url \'out\' %}}" target="_blank" {MARK}>fine</a>',
                '<a href="{% url \'in\' %}" target="_blank">fine</a>',
                f'<a href="{{% url \'in\' %}}" target="_blank" {MARK}>needless</a>',
                "<a href=\"{% url 'out' %}\">no new tab, nothing to do</a>",
                "{% url 'out' 1 as sheet %}",
                '<a href="{{ sheet }}" target="_blank" @click="() => 1 > 0">Sheet</a>',
                '<a href="{{ item.flyer_url }}" target="_blank">Flyer</a>',
                '<a href="{{ page.flyer.url }}" target="_blank">an upload, another host</a>',
                '<a href="{% url \'shelled\' %}" target="_blank">unknown base</a>',
                '<a href="{% url \'bare\' %}" target="_blank">unknown render</a>',
                '{% include "card.html" with flyer_url=sheet %}',
            ]
        )
        templates["card.html"] = '<a href="{{ flyer_url }}" target="_blank">Flyer</a>'
        assert _offenders_in(leaky, "leaky.html", templates, views) == [
            f"leaky.html:1 (out: a page without the hub navigation, missing {MARK})",
            f"leaky.html:4 (in: a page inside the hub, so {MARK} only strands the link in Safari)",
            f"leaky.html:7 (out: a page without the hub navigation, missing {MARK})",
            f"leaky.html:8 (a standalone page's URL, missing {MARK})",
            "leaky.html:10 (shelled: cannot tell what page it renders; register it in RENDERED_ELSEWHERE)",
            "leaky.html:11 (bare: cannot tell what page it renders; register it in RENDERED_ELSEWHERE)",
            f"card.html:1 (via leaky.html flyer_url=sheet; out: a page without the hub navigation, missing {MARK})",
        ]
