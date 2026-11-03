"""Repo-wide template lint: a new-tab link to a standalone page carries ``data-pl-new-window``.

Inside the native app ``static/js/native-new-tab-links.js`` opens a same-host
``target="_blank"`` link in place, because the iOS shell hands a new-window navigation to
Safari, which has no session (#1102). That is right for a page inside the hub, which has the
sidebar and top bar to leave by. A flyer, QR or sticker sheet, or print page is a document of
its own with no navigation, and iOS has no back swipe, so opened in place it strands the member;
``data-pl-new-window`` tells the script to leave such a link to the shell (FRONTEND.md rule 24).

Two halves, neither of which parses ``{% extends %}``. Lint A: every ``target="_blank"`` link
that names a URL in ``STANDALONE_URL_NAMES`` (a ``{% url %}`` in the tag, a ``{% url ... as var %}``
in the same template, or an ``{% include ... with param=var %}`` one level down) carries the mark,
an unmarked link whose variable is named for a flyer, sticker sheet, print or reconciliation page
(a URL built in Python, ``item.flyer_url``) fails too, and a mark on any other link fails, since
it sends a hub page back to a signed-out Safari. Lint B keeps the registry honest: every template
that is a whole document of its own (an ``<html>`` or doctype and no ``{% extends %}``) is listed
in ``KNOWN_STANDALONE_TEMPLATES``, so a new one fails until its author decides whether its URL
belongs in ``STANDALONE_URL_NAMES``.

Blind spots: a standalone URL built in Python under a name outside the backstop pattern, and a
URL an admin types into a link field (a hand-pasted flyer address) are not seen.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.urls import URLPattern, URLResolver, get_resolver

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
MARK = "data-pl-new-window"

#: URL names whose page is a document of its own, with no hub navigation to leave by.
STANDALONE_URL_NAMES: frozenset[str] = frozenset(
    {
        "billing_admin_reconciliation_print",
        "classes:class_flyer",
        "hub_equipment_flyer",
        "hub_event_flyer",
        "hub_guild_flyer",
        "hub_orientation_type_flyer",
        "hub_wiki_stickers",
        "hub_orientation_action",
        "signage_player",
    }
)

#: Every whole-document template outside the email folders, and why it is one.
KNOWN_STANDALONE_TEMPLATES: frozenset[str] = frozenset(
    {
        "base.html",  # the signed-out site's own base, not linked with _blank
        "hub/base.html",  # the hub chrome itself
        "allauth/layouts/base.html",  # allauth's layout override, not linked with _blank
        "components/loading_bar.html",  # a comment mentions <html>; an include, not linked
        "signage/base.html",  # signage_player (on SIGNAGE_BASE_URL, another host)
        "billing/reconciliation_print.html",  # billing_admin_reconciliation_print
        "classes/class_flyer.html",  # classes:class_flyer
        "hub/equipment_flyer.html",  # hub_equipment_flyer
        "hub/event_flyer.html",  # hub_event_flyer
        "hub/guild_flyer.html",  # hub_guild_flyer
        "hub/orientation_type_flyer.html",  # hub_orientation_type_flyer
        "hub/wiki_sticker_sheet.html",  # hub_wiki_stickers
        "hub/orientation_action.html",  # hub_orientation_action, an emailed link, not linked with _blank
    }
)

URL_REF = re.compile(r"{%\s*url\s+['\"]([^'\"]+)['\"]")
URL_AS = re.compile(r"{%\s*url\s+['\"]([^'\"]+)['\"][^%]*?\bas\s+(\w+)\s*%}")
INCLUDE = re.compile(r"{%\s*include\s+['\"]([^'\"]+)['\"]([^%]*)%}")
BLANK = re.compile(r"""\btarget\s*=\s*["']_blank["']""")
STANDALONE_VAR = re.compile(r"{{\s*[\w.]*(?:flyer|sticker|print|reconciliation)\w*_url\b")
WHOLE_DOCUMENT = re.compile(r"<!doctype|<html", re.IGNORECASE)


def _url_names() -> set[str]:
    names: set[str] = set()

    def walk(resolver: URLResolver, prefix: str) -> None:
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern, prefix + (f"{pattern.namespace}:" if pattern.namespace else ""))
            elif isinstance(pattern, URLPattern) and pattern.name:
                names.add(prefix + pattern.name)

    walk(get_resolver(), "")
    return names


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


def _verdict(tag: str, names: set[str]) -> str | None:
    """Why a tag pointing at ``names`` breaks the rule, or None when it is fine."""
    marked = MARK in tag
    standalone = sorted(names & STANDALONE_URL_NAMES)
    python_built = STANDALONE_VAR.search(tag) is not None
    if BLANK.search(tag) and not marked and (standalone or python_built):
        what = ", ".join(standalone) or "a standalone page's URL"
        return f"{what}: no hub navigation, missing {MARK}"
    if marked and not standalone and not python_built:
        return f"{MARK} on a link to no registered standalone page; add its URL name to STANDALONE_URL_NAMES or drop the mark"
    return None


def _offenders_in(text: str, template: str, templates: dict[str, str]) -> list[str]:
    assigned: dict[str, set[str]] = {}
    for name, var in URL_AS.findall(text):
        assigned.setdefault(var, set()).add(name)
    offenders = []
    for lineno, tag in _anchors(text):
        names = set(URL_REF.findall(tag)) | {n for var, ns in assigned.items() if _uses(var, tag) for n in ns}
        if reason := _verdict(tag, names):
            offenders.append(f"{template}:{lineno} ({reason})")
    # One level of include: a parameter fed from a {% url as %} lands on a tag in the component.
    for included, args in INCLUDE.findall(text):
        for param, var in re.findall(r"(\w+)=(\w+)", args):
            if not (assigned.get(var, set()) & STANDALONE_URL_NAMES) or included not in templates:
                continue
            for lineno, tag in _anchors(templates[included]):
                if _uses(param, tag) and (reason := _verdict(tag, assigned[var])):
                    offenders.append(f"{included}:{lineno} (via {template} {param}={var}; {reason})")
    return offenders


def _is_email(template: str) -> bool:
    return any(part.startswith("email") for part in template.split("/")[:-1])


def _whole_documents(templates: dict[str, str]) -> set[str]:
    return {
        name
        for name, text in templates.items()
        if not _is_email(name) and WHOLE_DOCUMENT.search(text) and "{% extends" not in text
    }


def _all_templates() -> dict[str, str]:
    return {
        path.relative_to(TEMPLATES_DIR).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(TEMPLATES_DIR.rglob("*.html"))
    }


def describe_new_window_links():
    def it_registers_only_routes_that_exist():
        assert sorted(STANDALONE_URL_NAMES - _url_names()) == []

    def it_marks_every_new_tab_link_to_a_standalone_page_and_no_other():
        templates = _all_templates()
        offenders = sorted({o for name, text in templates.items() for o in _offenders_in(text, name, templates)})
        assert not offenders, (
            f'A same-host target="_blank" link to a page without the hub navigation carries {MARK}, and '
            "no other link does, or the native app strands the member (FRONTEND.md rule 24):\n  "
            + "\n  ".join(offenders)
        )

    def it_knows_every_template_that_is_a_document_of_its_own():
        found = _whole_documents(_all_templates())
        assert found == KNOWN_STANDALONE_TEMPLATES, (
            "A template with its own <html> and no {% extends %} has no hub navigation. Add it to "
            "KNOWN_STANDALONE_TEMPLATES in tests/new_window_links_lint_spec.py and, if anything links to it "
            f'with target="_blank", add its URL name to STANDALONE_URL_NAMES.\n  new: {sorted(found - KNOWN_STANDALONE_TEMPLATES)}'
            f"\n  stale: {sorted(KNOWN_STANDALONE_TEMPLATES - found)}"
        )

    def it_actually_detects_an_unmarked_standalone_link_and_a_marked_hub_link():
        # Self-test so a refactor can't quietly neuter the lint.
        leaky = "\n".join(
            [
                '<a href="{% url \'hub_guild_flyer\' 1 %}" target="_blank">Flyer</a>',
                f'<a href="{{% url \'hub_guild_flyer\' 1 %}}" target="_blank" {MARK}>fine</a>',
                '<a href="{% url \'hub_help\' %}" target="_blank">fine</a>',
                f'<a href="{{% url \'hub_help\' %}}" target="_blank" {MARK}>needless</a>',
                "<a href=\"{% url 'hub_guild_flyer' 1 %}\">no new tab, nothing to do</a>",
                "{% url 'hub_event_flyer' 1 as sheet %}",
                '<a href="{{ sheet }}" target="_blank" @click="() => 1 > 0">Sheet</a>',
                '<a href="{{ item.flyer_url }}" target="_blank">Flyer</a>',
                f'<a href="{{{{ item.flyer_url }}}}" target="_blank" {MARK}>fine</a>',
                '<a href="{{ page.flyer.url }}" target="_blank">an upload, another host</a>',
                '{% include "card.html" with link_url=sheet %}',
            ]
        )
        templates = {"card.html": '<a href="{{ link_url }}" target="_blank">Flyer</a>'}
        assert _offenders_in(leaky, "leaky.html", templates) == [
            f"leaky.html:1 (hub_guild_flyer: no hub navigation, missing {MARK})",
            f"leaky.html:4 ({MARK} on a link to no registered standalone page; add its URL name to "
            "STANDALONE_URL_NAMES or drop the mark)",
            f"leaky.html:7 (hub_event_flyer: no hub navigation, missing {MARK})",
            f"leaky.html:8 (a standalone page's URL: no hub navigation, missing {MARK})",
            f"card.html:1 (via leaky.html link_url=sheet; hub_event_flyer: no hub navigation, missing {MARK})",
        ]

    def it_never_reads_a_hub_page_as_standalone():
        # The registry is explicit, so an extends the spec cannot parse
        # ({% extends guilds_page_base|default:"hub/base.html" %}) cannot misclassify a page.
        assert "hub_help" not in STANDALONE_URL_NAMES
        assert "hub_spaces" not in STANDALONE_URL_NAMES

    def it_counts_a_new_whole_document_and_skips_email_and_extended_templates():
        templates = {
            "hub/new_sheet.html": "<!DOCTYPE html><html></html>",
            "hub/page.html": '{% extends "hub/base.html" %}<html>',
            "classes/emails/note.html": "<html></html>",
            "account/email/code.html": "<HTML></HTML>",
        }
        assert _whole_documents(templates) == {"hub/new_sheet.html"}
