"""Repo-wide template lint: a link to a download opts out of hx-boost (FRONTEND.md rule 24).

``hub/base.html`` boosts every same-origin anchor and form under its body, so a link to an
attachment is fetched over XHR and the file's raw text is swapped into the page in place of
a download, with the file's URL in the address bar. htmx 2.0.4 does not look at the
``download`` attribute, so that alone changes nothing. A member found this on the event
page's Add to calendar; every QR download and CSV export in the portal had the same defect.
``tests/e2e/event_ics_download_spec.py`` drives a few real clicks; this is the lock across
the whole tree.

Two halves. ``ATTACHMENT_SOURCES`` names every function in the source tree that sets a
``Content-Disposition`` header and the URL names its responses answer to; the source walk
fails when a function appears that is not registered, so a new attachment view has to be
added here and its links then fall under the rule. Then every ``<a>`` and ``<form>`` in
``templates/`` that points at one of those URL names, directly, through a
``{% url ... as var %}`` in the same template, or through an ``{% include ... with
param=var %}`` one level down, or that carries a ``download`` attribute, must carry
``hx-boost="false"`` and, when it points at one of our own attachments, ``data-pl-download``,
which ``static/js/native-downloads.js`` uses to hide it inside the native app (the shells
drop a download). Both attributes are inert on a page that is not boosted and in a browser,
so the rule applies everywhere and no template has to know its base.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from django.urls import get_resolver

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
SKIP_PARTS = {".venv", "node_modules", "tests", "spec", "migrations", "staticfiles"}

#: (file, top-level function) that sets Content-Disposition -> the URL names it answers to.
ATTACHMENT_SOURCES: dict[tuple[str, str], frozenset[str]] = {
    ("hub/views.py", "guild_qr_download"): frozenset({"hub_guild_qr"}),
    ("hub/equipment_views.py", "hub_equipment_qr"): frozenset({"hub_equipment_qr"}),
    ("hub/orientations_views.py", "hub_orientation_type_qr"): frozenset({"hub_orientation_type_qr"}),
    ("hub/views.py", "calendar_export_ics"): frozenset({"hub_calendar_export_ics"}),
    ("hub/views.py", "event_ics"): frozenset({"hub_event_ics"}),
    ("hub/views.py", "event_qr"): frozenset({"hub_event_qr"}),
    ("hub/wiki_views.py", "hub_wiki_qr_download"): frozenset({"hub_wiki_qr_download"}),
    ("classes/views.py", "class_qr_download"): frozenset({"classes:class_qr"}),
    ("classes/exports.py", "stream_registrations_query_csv"): frozenset({"classes:admin_registrations_export"}),
    ("classes/exports.py", "stream_instructor_inquiries_csv"): frozenset({"classes:admin_instructor_inquiries_export"}),
    ("membership/orientation_exports.py", "stream_orientations_csv"): frozenset({"hub_orientations_export"}),
    ("billing/reconciliation.py", "stream_reconciliation_csv"): frozenset({"billing_admin_reconciliation_csv"}),
    ("billing/payments_panel.py", "stream_payments_csv"): frozenset({"billing_admin_payments_csv"}),
    # Retired: billing_admin_reports_csv now redirects to the reconciliation CSV.
    ("billing/reports.py", "stream_report_csv"): frozenset(),
}
ATTACHMENT_URL_NAMES: frozenset[str] = frozenset().union(*ATTACHMENT_SOURCES.values())

OPT_OUT = 'hx-boost="false"'
MARK = "data-pl-download"
URL_REF = re.compile(r"{%\s*url\s+['\"]([^'\"]+)['\"]")
URL_AS = re.compile(r"{%\s*url\s+['\"]([^'\"]+)['\"][^%]*?\bas\s+(\w+)\s*%}")
INCLUDE = re.compile(r"{%\s*include\s+['\"]([^'\"]+)['\"]([^%]*)%}")
BARE_DOWNLOAD = re.compile(r"(?<![\w-])download(?![\w-])")


def _attachment_functions() -> set[tuple[str, str]]:
    """Every (file, function) in the source tree whose body sets a Content-Disposition header.

    Top-level functions and methods of top-level classes; a nested helper (the ``iter_rows``
    inside a streaming export) is reported under the function that encloses it.
    """
    found: set[tuple[str, str]] = set()
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if SKIP_PARTS & set(rel.parts):
            continue
        source = path.read_text(encoding="utf-8")
        if "Content-Disposition" not in source:
            continue
        tree = ast.parse(source)
        candidates = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                candidates.append(node)
            elif isinstance(node, ast.ClassDef):
                candidates += [n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        for node in candidates:
            if "Content-Disposition" in (ast.get_source_segment(source, node) or ""):
                found.add((rel.as_posix(), node.name))
    return found


def _url_name_exists(name: str) -> bool:
    resolver = get_resolver()
    *namespaces, leaf = name.split(":")
    for namespace in namespaces:
        if namespace not in resolver.namespace_dict:
            return False
        resolver = resolver.namespace_dict[namespace][1]
    return leaf in resolver.reverse_dict


def _tags(text: str) -> list[tuple[int, str]]:
    """Every ``<a …>`` / ``<form …>`` opening tag with its 1-based line.

    Quote-aware, so a ``>`` inside an attribute value (an Alpine arrow function) does not
    end the tag early.
    """
    tags = []
    for match in re.finditer(r"<(?:a|form)(?=[\s>])", text, re.IGNORECASE):
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


def _blank_quoted(tag: str) -> str:
    return re.sub(r"\"[^\"]*\"|'[^']*'", '""', tag)


def _uses(var: str, tag: str) -> bool:
    return re.search(r"{{\s*" + re.escape(var) + r"\b", tag) is not None


def _missing(tag: str, required: tuple[str, ...]) -> str:
    return " ".join(attr for attr in required if attr not in tag)


def _offenders_in(text: str, template: str, templates: dict[str, str]) -> list[str]:
    """Rule violations in one template. ``templates`` maps a template name to its text for includes.

    A tag that points at one of our attachments needs both the opt-out and the marker; a tag
    that only carries a ``download`` attribute (an uploaded document on another host, which
    the native shells hand to the system) needs the opt-out alone.
    """
    assigned = {var: name for name, var in URL_AS.findall(text) if name in ATTACHMENT_URL_NAMES}
    offenders = []
    for lineno, tag in _tags(text):
        reasons: list[str]
        required: tuple[str, ...]
        attachments = sorted(
            {n for n in URL_REF.findall(tag) if n in ATTACHMENT_URL_NAMES}
            | {assigned[v] for v in assigned if _uses(v, tag)}
        )
        if attachments:
            reasons, required = attachments, (OPT_OUT, MARK)
        elif BARE_DOWNLOAD.search(_blank_quoted(tag)):
            reasons, required = ["download attribute"], (OPT_OUT,)
        else:
            continue
        if missing := _missing(tag, required):
            offenders.append(f"{template}:{lineno} ({', '.join(reasons)}) missing {missing}")
    # One level of include: a parameter fed from an attachment URL must land on a marked tag there.
    for included, args in INCLUDE.findall(text):
        for param, var in re.findall(r"(\w+)=(\w+)", args):
            if var not in assigned or included not in templates:
                continue
            for lineno, tag in _tags(templates[included]):
                if _uses(param, tag) and (missing := _missing(tag, (OPT_OUT, MARK))):
                    offenders.append(
                        f"{included}:{lineno} ({assigned[var]} via {template} {param}={var}) missing {missing}"
                    )
    return offenders


def _all_templates() -> dict[str, str]:
    return {
        path.relative_to(TEMPLATES_DIR).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(TEMPLATES_DIR.rglob("*.html"))
    }


def describe_download_links():
    def it_registers_every_function_that_sets_content_disposition():
        found = _attachment_functions()
        assert found == set(ATTACHMENT_SOURCES), (
            "ATTACHMENT_SOURCES in tests/download_links_lint_spec.py must list exactly the functions that set "
            f"Content-Disposition.\n  unregistered: {sorted(found - set(ATTACHMENT_SOURCES))}"
            f"\n  stale: {sorted(set(ATTACHMENT_SOURCES) - found)}"
        )

    def it_names_only_routes_that_exist():
        missing = [name for name in sorted(ATTACHMENT_URL_NAMES) if not _url_name_exists(name)]
        assert not missing, f"ATTACHMENT_SOURCES names URL routes that do not exist: {missing}"

    def it_opts_every_download_link_out_of_hx_boost():
        templates = _all_templates()
        offenders = sorted({o for name, text in templates.items() for o in _offenders_in(text, name, templates)})
        assert not offenders, (
            'A link to a download under the boosted hub body needs hx-boost="false", or htmx swaps the '
            "file's text into the page instead of downloading it (FRONTEND.md rule 24):\n  " + "\n  ".join(offenders)
        )

    def it_actually_detects_a_boosted_or_unmarked_download_link():
        # Self-test so a refactor can't quietly neuter the lint.
        leaky = "\n".join(
            [
                "{% url 'hub_event_ics' 1 as ics %}",
                '<a href="{{ ics }}">Add</a>',
                "<a href=\"{% url 'hub_guild_qr' 1 'svg' %}\" hx-boost=\"false\" data-pl-download>fine</a>",
                '<a href="/x" @click="() => 1 > 0" download>doc</a>',
                '<a class="download" title="Download the app">not a download</a>',
                "<form action=\"{% url 'hub_calendar_export_ics' %}\"></form>",
                "<a href=\"{% url 'hub_guild_qr' 1 'png' %}\" hx-boost=\"false\">opted out, not marked</a>",
                '<a href="/doc.pdf" download hx-boost="false">a document needs no marker</a>',
            ]
        )
        assert _offenders_in(leaky, "leaky.html", {}) == [
            'leaky.html:2 (hub_event_ics) missing hx-boost="false" data-pl-download',
            'leaky.html:4 (download attribute) missing hx-boost="false"',
            'leaky.html:6 (hub_calendar_export_ics) missing hx-boost="false" data-pl-download',
            "leaky.html:7 (hub_guild_qr) missing data-pl-download",
        ]

    def it_follows_an_attachment_url_into_an_included_component():
        card = "\n".join(
            [
                '<a href="{{ svg_url }}">QR</a>',
                '<a href="{{ png_url }}" hx-boost="false">QR</a>',
                '<a href="{{ pdf_url }}" hx-boost="false" data-pl-download>fine</a>',
            ]
        )
        caller = (
            "{% url 'hub_event_qr' 1 'svg' as u %}\n"
            "{% include \"card.html\" with svg_url=u png_url=u|add:'?x' pdf_url=u %}"
        )
        assert _offenders_in(caller, "caller.html", {"card.html": card}) == [
            'card.html:1 (hub_event_qr via caller.html svg_url=u) missing hx-boost="false" data-pl-download',
            "card.html:2 (hub_event_qr via caller.html png_url=u) missing data-pl-download",
        ]
