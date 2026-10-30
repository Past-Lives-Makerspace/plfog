"""``{% tab_picker %}``: a tab row that becomes a section picker on a phone (#852).

Wrap a page's ``.pl-tabs`` row in the tag and it renders the row exactly as written (the
desktop strip, unchanged at 640px and up) followed by ``components/tab_picker.html``: one full
width row naming the group, the current section and "n of N", that opens a list of every
section. CSS shows one or the other at the 640px line.

The picker reads its sections out of the row the tag just rendered rather than from a second
list, so it can never offer a tab the row does not, and every ``{% if %}`` that decides which
tabs a viewer gets stays where it is. A row of links (each tab an ``<a href>``) is read whole
here, the current one by ``vote-tab--active`` or ``aria-current="page"``, so the picker works
before Alpine starts and without it. A row of in-page buttons (an Alpine component switching
panes) only knows its current tab in the browser, so the picker's ``plTabPicker`` component
mirrors the row from then on (``static/js/tab_picker.js``); what is read here is the first
paint. A row with fewer than two tabs offers no choice and gets no picker, as the per-class
strip already does.

A composer's step bar (``.pl-phase-tabs`` of ``.pl-phase-tab`` buttons) is read the same way, so a
step bar is a picker on a phone too rather than a row sliding off the edge. Its done and error
marks (a direct child with a ``__mark`` class, ``pl-phase-tab__mark``) are not part of the label; the browser mirror
carries them into the list.

Usage::

    {% load tab_picker %}
    {% tab_picker "Guild Sections" %}
    <nav role="tablist" class="pl-tabs">...</nav>
    {% endtab_picker %}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from django import template
from django.template.base import FilterExpression, NodeList, Parser, Token
from django.template.context import Context
from django.template.loader import render_to_string
from django.utils.safestring import SafeString

register = template.Library()

#: "Registrations (3)": a count a template writes into a link's label, split off so the picker
#: can show it apart. Never applied to a row of buttons, whose labels are often typed by a
#: person ("Board (2)", an FAQ label "FAQ (2025)").
_LABEL_COUNT = re.compile(r"^(?P<label>.+?)\s*\((?P<count>\d+)\)$")

#: The classes that make an element a row, and a direct child of a row a tab.
ROW_CLASSES = frozenset({"pl-tabs", "pl-phase-tabs"})
TAB_CLASSES = frozenset({"vote-tab", "pl-phase-tab"})

#: A row of links that swap in place rather than navigate (What's New's tabs inside its modal)
#: carries these from each link onto the picker's item for it, so a phone switches exactly as the
#: desktop row does and closing the modal never strands the member on another page.
HX_ATTRIBUTES = ("hx-get", "hx-target", "hx-swap")

#: Elements with no end tag, which must not open a level of nesting.
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)


@dataclass(frozen=True)
class PickerTab:
    """One section in the picker, read off one ``.vote-tab`` of the row."""

    label: str
    count: str
    href: str
    active: bool
    #: The count element's own aria-label ("3 waiting"), so a screen reader hears what it counts.
    count_label: str = ""
    #: The link's :data:`HX_ATTRIBUTES`, in that order, those it carries; rendered on the item as written.
    hx: tuple[tuple[str, str], ...] = ()


@dataclass
class _Reading:
    """The tab being read: its attributes so far and the text inside it."""

    href: str
    active: bool
    label: list[str] = field(default_factory=list)
    count: list[str] = field(default_factory=list)
    count_label: str = ""
    hx: tuple[tuple[str, str], ...] = ()


class _RowReader(HTMLParser):
    """Collects the tab children of the first row (``ROW_CLASSES``) in a fragment.

    Only the row's direct children are tabs. Text inside a direct child of a tab whose class names
    a count (``pl-kiln-count``, ``vote-tab__count``) is the tab's count, everything else its
    label, and text inside one with a ``__mark`` class (a step's done tick) is neither; the same
    reading as ``readTab`` in ``static/js/tab_picker.js``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tabs: list[PickerTab] = []
        self.buttons = 0
        self.found_row = False
        self._depth = 0
        self._row_depth: int | None = None
        self._tab: _Reading | None = None
        self._tab_depth = 0
        self._count_depth: int | None = None
        self._mark_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        classes = values.get("class", "").split()
        if tag not in _VOID:
            self._depth += 1
        if self._row_depth is None:
            if not self.found_row and ROW_CLASSES.intersection(classes):
                self.found_row = True
                self._row_depth = self._depth
            return
        if self._tab is None:
            if self._depth == self._row_depth + 1 and TAB_CLASSES.intersection(classes):
                self._start_tab(tag, values, classes)
            return
        if self._depth != self._tab_depth + 1 or self._count_depth is not None or self._mark_depth is not None:
            return
        if any(name.endswith("__mark") for name in classes):
            self._mark_depth = self._depth
        elif any("count" in name for name in classes):
            self._count_depth = self._depth
            self._tab.count_label = values.get("aria-label", "")

    def _start_tab(self, tag: str, values: dict[str, str], classes: list[str]) -> None:
        hx: tuple[tuple[str, str], ...] = ()
        if tag == "a":
            if "href" not in values:
                raise ValueError("{% tab_picker %} tab link has no href")
            href = values["href"]
            hx = tuple((name, values[name]) for name in HX_ATTRIBUTES if name in values)
        else:
            href = ""
            self.buttons += 1
        active = (
            "vote-tab--active" in classes
            or values.get("aria-current") == "page"
            or values.get("aria-selected") == "true"
        )
        self._tab = _Reading(href=href, active=active, hx=hx)
        self._tab_depth = self._depth

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if self._count_depth == self._depth:
            self._count_depth = None
        if self._mark_depth == self._depth:
            self._mark_depth = None
        if self._tab is not None and self._depth == self._tab_depth:
            self.tabs.append(_finish(self._tab))
            self._tab = None
        if self._row_depth == self._depth:
            self._row_depth = None
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._tab is None or self._mark_depth is not None:
            return
        (self._tab.label if self._count_depth is None else self._tab.count).append(data)


def _finish(reading: _Reading) -> PickerTab:
    label = " ".join("".join(reading.label).split())
    count = "".join(reading.count).strip()
    written = _LABEL_COUNT.match(label) if reading.href else None
    if written:
        label, count = written["label"], written["count"]
    return PickerTab(
        label=label,
        count=count,
        href=reading.href,
        active=reading.active,
        count_label=reading.count_label,
        hx=reading.hx,
    )


def read_tabs(row_html: str) -> tuple[list[PickerTab], bool]:
    """The tabs of the first row (``.pl-tabs`` or ``.pl-phase-tabs``) in ``row_html``, and whether they are in-page buttons.

    Raises:
        ValueError: when there is no row, the row mixes links and buttons (the
            picker would not know whether to navigate or switch a pane), or a tab link has no href.
    """
    reader = _RowReader()
    reader.feed(row_html)
    reader.close()
    if not reader.found_row:
        raise ValueError("{% tab_picker %} wraps no .pl-tabs or .pl-phase-tabs row")
    if reader.buttons not in (0, len(reader.tabs)):
        raise ValueError("{% tab_picker %} row mixes links and buttons")
    return reader.tabs, reader.buttons > 0


class TabPickerNode(template.Node):
    """Renders the wrapped row, then the picker built from it."""

    def __init__(self, eyebrow: FilterExpression, nodelist: NodeList) -> None:
        self.eyebrow = eyebrow
        self.nodelist = nodelist

    def render(self, context: Context) -> SafeString:
        row_html = self.nodelist.render(context)
        tabs, buttons = read_tabs(row_html)
        if len(tabs) < 2:
            return row_html
        # A row of buttons with no current tab yet (Alpine marks it) paints the first until it does.
        current = next((i for i, tab in enumerate(tabs) if tab.active), 0 if buttons else None)
        picker = render_to_string(
            "components/tab_picker.html",
            {
                "eyebrow": self.eyebrow.resolve(context),
                "tabs": tabs,
                "buttons": buttons,
                "current": tabs[current] if current is not None else None,
                "position": current + 1 if current is not None else None,
                "total": len(tabs),
            },
        )
        # Both halves are rendered templates, already safe, and safe plus safe stays safe.
        return row_html + picker


@register.tag("tab_picker")
def tab_picker(parser: Parser, token: Token) -> TabPickerNode:
    """``{% tab_picker "<eyebrow>" %}<row>{% endtab_picker %}``; the eyebrow names the group."""
    bits = token.split_contents()
    if len(bits) != 2:
        raise template.TemplateSyntaxError("{% tab_picker %} takes one argument, the eyebrow")
    nodelist = parser.parse(("endtab_picker",))
    parser.delete_first_token()
    return TabPickerNode(parser.compile_filter(bits[1]), nodelist)
