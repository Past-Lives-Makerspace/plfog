"""Specs for ``{% tab_picker %}`` (#852): the phone section picker read off a ``.pl-tabs`` row."""

from __future__ import annotations

import pytest
from django.template import Context, Template, TemplateSyntaxError

from core.templatetags.tab_picker import PickerTab, read_tabs

LINKS = (
    '<nav class="pl-tabs" aria-label="Kiln pages">'
    '<a href="/kiln/" class="vote-tab">My Tickets</a>'
    '<a href="/kiln/load/" class="vote-tab vote-tab--active" aria-current="page">Load the Kiln'
    '<span class="pl-kiln-count" aria-label="3 waiting">3</span></a>'
    '<a href="/kiln/log/?a=1&amp;b=2" class="vote-tab">Kiln Log</a>'
    "</nav>"
)
BUTTONS = (
    '<div x-data="{ section: \'one\' }"><nav role="tablist" class="pl-tabs">'
    "<button type=\"button\" class=\"vote-tab\" :class=\"section === 'one' ? 'vote-tab--active' : ''\" "
    "@click=\"section = 'one'\">\n    One\n</button>"
    '<button type="button" class="vote-tab" @click="section = \'two\'">Two</button>'
    "</nav></div>"
)


def _render(row: str, eyebrow: str = '"Kiln"', **context: object) -> str:
    source = "{% load tab_picker %}{% tab_picker " + eyebrow + " %}" + row + "{% endtab_picker %}"
    return Template(source).render(Context(context))


def describe_read_tabs():
    def it_reads_each_link_its_label_count_and_whether_it_is_current():
        tabs, buttons = read_tabs(LINKS)
        assert buttons is False
        assert tabs == [
            PickerTab(label="My Tickets", count="", href="/kiln/", active=False),
            PickerTab(label="Load the Kiln", count="3", href="/kiln/load/", active=True),
            PickerTab(label="Kiln Log", count="", href="/kiln/log/?a=1&b=2", active=False),
        ]

    def it_splits_a_count_written_into_the_label():
        tabs, _ = read_tabs('<nav class="pl-tabs"><a href="/r/" class="vote-tab">Registrations (12)</a></nav>')
        assert tabs == [PickerTab(label="Registrations", count="12", href="/r/", active=False)]

    def it_reads_in_page_buttons_and_collapses_their_whitespace():
        tabs, buttons = read_tabs(BUTTONS)
        assert buttons is True
        assert [(tab.label, tab.href, tab.active) for tab in tabs] == [("One", "", False), ("Two", "", False)]

    @pytest.mark.parametrize(
        "attrs",
        [
            'class="vote-tab vote-tab--active"',
            'class="vote-tab" aria-current="page"',
            'class="vote-tab" aria-selected="true"',
        ],
    )
    def it_takes_the_current_tab_from_any_of_the_three_marks(attrs):
        tabs, _ = read_tabs(f'<nav class="pl-tabs"><button {attrs}>A</button><button class="vote-tab">B</button></nav>')
        assert [tab.active for tab in tabs] == [True, False]

    def it_reads_only_the_rows_direct_tabs_and_only_the_first_row():
        row = (
            '<p class="vote-tab">before</p>'
            '<nav class="pl-tabs"><a href="/a/" class="vote-tab">A <br><img src="x.png"/><span>still A</span></a>'
            '<span class="hint"><a href="/x/" class="vote-tab">nested</a></span>'
            '<a href="/b/" class="vote-tab">B</a></nav>'
            '<nav class="pl-tabs"><a href="/c/" class="vote-tab">C</a></nav>'
        )
        tabs, _ = read_tabs(row)
        assert [(tab.label, tab.href) for tab in tabs] == [("A still A", "/a/"), ("B", "/b/")]

    def it_refuses_markup_with_no_row():
        with pytest.raises(ValueError, match="wraps no .pl-tabs row"):
            read_tabs('<nav class="pl-wp-chiprow"><a href="/" class="vote-tab">A</a></nav>')

    def it_refuses_a_row_that_mixes_links_and_buttons():
        with pytest.raises(ValueError, match="mixes links and buttons"):
            read_tabs(
                '<nav class="pl-tabs"><a href="/" class="vote-tab">A</a><button class="vote-tab">B</button></nav>'
            )


def describe_the_tab_picker_tag():
    def it_renders_the_row_untouched_then_the_picker():
        html = _render(LINKS)
        assert html.startswith(LINKS)
        assert html.index('<details class="pl-tab-picker"') > len(LINKS) - 1

    def it_names_the_group_the_current_section_and_its_position():
        html = _render(LINKS)
        assert 'aria-label="Kiln: Load the Kiln, section 2 of 3"' in html
        assert '<span class="pl-tab-picker__eyebrow">Kiln</span>' in html
        assert '<span class="pl-tab-picker__current">Load the Kiln (3)</span>' in html
        assert '<span class="pl-tab-picker__count">2 of 3</span>' in html

    def it_lists_every_link_and_marks_only_the_current_one():
        html = _render(LINKS)
        menu = html[html.index('<nav class="pl-tab-picker__menu" aria-label="Kiln">') :]
        assert menu.count('class="pl-tab-picker__item"') == 3
        assert menu.count('aria-current="page"') == 1
        assert menu.count('class="pl-tab-picker__check"') == 1
        assert 'href="/kiln/log/?a=1&amp;b=2"' in menu
        assert '<span class="pl-tab-picker__item-count">3</span>' in menu
        assert "data-tab-picker-buttons" not in html
        assert "x-for" not in html

    def it_says_choose_a_section_when_no_link_is_current():
        html = _render(
            '<nav class="pl-tabs"><a href="/a/" class="vote-tab">A</a><a href="/b/" class="vote-tab">B</a></nav>'
        )
        assert 'aria-label="Kiln: 2 sections"' in html
        assert '<span class="pl-tab-picker__current">Choose a section</span>' in html
        assert '<span class="pl-tab-picker__count">2</span>' in html

    def it_hands_a_button_row_to_the_mirror_and_paints_its_first_tab_until_then():
        html = _render(BUTTONS, '"Guild Sections"')
        assert "data-tab-picker-buttons" in html
        assert ':aria-label="summaryLabel()"' in html
        assert 'x-text="currentText()">One</span>' in html
        assert 'x-text="positionText()">1 of 2</span>' in html
        assert '<template x-for="(item, i) in items" :key="i">' in html
        assert 'role="group" aria-label="Guild Sections"' in html

    def it_paints_a_button_rows_server_marked_tab():
        row = '<nav class="pl-tabs"><button class="vote-tab">A</button><button class="vote-tab vote-tab--active">B</button></nav>'
        assert 'x-text="positionText()">2 of 2</span>' in _render(row)

    def it_takes_the_eyebrow_from_a_variable():
        assert '<span class="pl-tab-picker__eyebrow">Manage Lathe</span>' in _render(LINKS, "name", name="Manage Lathe")

    def it_adds_nothing_to_a_row_with_one_tab():
        row = '<nav class="pl-tabs"><a href="/a/" class="vote-tab vote-tab--active">Only</a></nav>'
        assert _render(row) == row

    def it_takes_exactly_one_argument():
        with pytest.raises(TemplateSyntaxError, match="takes one argument"):
            Template("{% load tab_picker %}{% tab_picker %}{% endtab_picker %}")
