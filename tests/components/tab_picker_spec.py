"""Specs for ``{% tab_picker %}`` (#852): the phone section picker read off a ``.pl-tabs`` row or a step bar."""

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

BADGED = (
    '<nav class="pl-tabs"><a href="/a/" class="vote-tab vote-tab--active">Overview</a>'
    '<a href="/b/" class="vote-tab"><span class="vote-tab__label">Cubbies'
    '<span class="pl-new" data-new-key="guild.cubbies">New!</span></span></a>'
    '<a href="/c/" class="vote-tab">Wiki</a></nav>'
)
BADGED_BUTTONS = (
    '<nav class="pl-tabs"><button class="vote-tab vote-tab--active">Overview</button>'
    '<button class="vote-tab">Cubbies<span class="pl-new" data-new-key="guild.cubbies">New!</span></button></nav>'
)
#: A row of links that swap a pane in place rather than navigate (What's New's tabs inside its modal).
SWAPPING = (
    '<nav class="pl-tabs">'
    '<a href="/w/?tab=a" class="vote-tab vote-tab--active" aria-current="page" hx-get="/w/?tab=a" hx-target="#body" hx-swap="innerHTML">A</a>'
    '<a href="/w/?tab=b" class="vote-tab" hx-get="/w/?tab=b" data-x="1">B</a>'
    "</nav>"
)

STEPS = (
    '<div class="pl-phase-tabs" role="tablist" aria-label="Class steps">'
    '<button type="button" class="pl-phase-tab pl-phase-tab--done" role="tab" data-step-tab="1">Basics'
    '<span class="pl-phase-tab__mark" aria-hidden="true" x-show="!errorSteps.includes(1)">&#10003;</span>'
    '<span class="pl-phase-tab__mark" aria-hidden="true" x-show="errorSteps.includes(1)" x-cloak>&#9679;</span></button>'
    '<button type="button" class="pl-phase-tab" role="tab" data-step-tab="2">Photos'
    '<span class="pl-phase-tab__mark" aria-hidden="true" x-cloak>&#9679;</span></button>'
    "</div>"
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
            PickerTab(label="Load the Kiln", count="3", href="/kiln/load/", active=True, count_label="3 waiting"),
            PickerTab(label="Kiln Log", count="", href="/kiln/log/?a=1&b=2", active=False),
        ]

    def it_splits_a_count_written_into_the_label():
        tabs, _ = read_tabs('<nav class="pl-tabs"><a href="/r/" class="vote-tab">Registrations (12)</a></nav>')
        assert tabs == [PickerTab(label="Registrations", count="12", href="/r/", active=False)]

    def it_never_splits_a_count_out_of_a_buttons_typed_label():
        tabs, _ = read_tabs(
            '<nav class="pl-tabs"><button class="vote-tab">Board (2)</button><button class="vote-tab">FAQ (2025)</button></nav>'
        )
        assert [(tab.label, tab.count) for tab in tabs] == [("Board (2)", ""), ("FAQ (2025)", "")]

    def it_counts_only_a_direct_child_of_the_tab():
        row = '<nav class="pl-tabs"><a href="/a/" class="vote-tab">A <em>new <span class="x-count">4</span></em></a></nav>'
        tabs, _ = read_tabs(row)
        assert tabs == [PickerTab(label="A new 4", count="", href="/a/", active=False)]

    def it_refuses_a_tab_link_with_no_href():
        with pytest.raises(ValueError, match="tab link has no href"):
            read_tabs('<nav class="pl-tabs"><a class="vote-tab">A</a></nav>')

    def it_carries_a_links_htmx_attributes_and_nothing_else_of_its_attributes():
        tabs, _ = read_tabs(SWAPPING)
        assert tabs[0].hx == (("hx-get", "/w/?tab=a"), ("hx-target", "#body"), ("hx-swap", "innerHTML"))
        assert tabs[1].hx == (("hx-get", "/w/?tab=b"),)
        assert read_tabs(LINKS)[0][0].hx == ()

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

    def it_reads_a_step_bar_and_leaves_its_marks_out_of_the_label():
        tabs, buttons = read_tabs(STEPS)
        assert buttons
        assert [tab.label for tab in tabs] == ["Basics", "Photos"]
        assert [tab.count for tab in tabs] == ["", ""]

    def it_reads_a_new_badge_as_the_tabs_key_and_keeps_it_out_of_the_label():
        tabs, _ = read_tabs(BADGED)
        assert [(tab.label, tab.new) for tab in tabs] == [("Overview", ""), ("Cubbies", "guild.cubbies"), ("Wiki", "")]
        tabs, _ = read_tabs(BADGED_BUTTONS)
        assert [(tab.label, tab.new) for tab in tabs] == [("Overview", ""), ("Cubbies", "guild.cubbies")]

    def it_refuses_markup_with_no_row():
        with pytest.raises(ValueError, match="wraps no .pl-tabs or .pl-phase-tabs row"):
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
        assert (
            '<span class="pl-tab-picker__item-count" aria-hidden="true">3</span><span class="sr-only">3 waiting</span>'
            in menu
        )
        assert "data-tab-picker-buttons" not in html
        assert "x-for" not in html

    def it_puts_a_links_htmx_attributes_on_its_item_so_a_phone_swaps_in_place_too():
        menu = _render(SWAPPING, '"What\'s New"')[len(SWAPPING) :]
        assert (
            'href="/w/?tab=a" class="pl-tab-picker__item" aria-current="page" hx-get="/w/?tab=a" hx-target="#body" hx-swap="innerHTML" @click="close()"'
            in menu
        )
        assert 'href="/w/?tab=b" class="pl-tab-picker__item" hx-get="/w/?tab=b" @click="close()"' in menu
        assert "hx-" not in _render(LINKS)[len(LINKS) :]

    def it_shows_a_count_with_no_label_as_it_is():
        row = '<nav class="pl-tabs"><a href="/r/" class="vote-tab vote-tab--active">Registrations (2)</a><a href="/w/" class="vote-tab">Waitlist</a></nav>'
        assert '<span class="pl-tab-picker__item-count">2</span>\n' in _render(row)

    def it_escapes_what_the_row_carried_in_the_items_hrefs_and_trigger_name():
        row = (
            '<nav class="pl-tabs">'
            '<a href="/a/?q=&quot;&gt;&lt;script&gt;" class="vote-tab vote-tab--active">&lt;script&gt;alert(1)&lt;/script&gt; &quot;x&quot;</a>'
            '<a href="/b/" class="vote-tab">B</a></nav>'
        )
        picker = _render(row)[len(row) :]
        assert "<script>" not in picker
        assert 'aria-label="Kiln: &lt;script&gt;alert(1)&lt;/script&gt; &quot;x&quot;, section 1 of 2"' in picker
        assert 'href="/a/?q=&quot;&gt;&lt;script&gt;"' in picker
        assert (
            '<span class="pl-tab-picker__item-label">&lt;script&gt;alert(1)&lt;/script&gt; &quot;x&quot;</span>'
            in picker
        )

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

    def it_turns_a_step_bar_into_a_picker_after_it():
        html = _render(STEPS, '"Class Steps"')
        assert html.startswith(STEPS)
        assert "data-tab-picker-buttons" in html
        assert 'x-text="currentText()">Basics</span>' in html
        assert 'x-text="positionText()">1 of 2</span>' in html
        assert 'x-text="item.mark"' in html

    def it_paints_a_button_rows_server_marked_tab():
        row = '<nav class="pl-tabs"><button class="vote-tab">A</button><button class="vote-tab vote-tab--active">B</button></nav>'
        assert 'x-text="positionText()">2 of 2</span>' in _render(row)

    def it_carries_a_link_rows_badge_into_its_item_and_dots_the_summary_while_another_tab_is_new():
        picker = _render(BADGED, '"Guild Sections"')[len(BADGED) :]
        assert '<span class="pl-new-dot" aria-hidden="true" data-tab-picker-dot></span>' in picker
        assert (
            '<span class="pl-tab-picker__item-label">Cubbies<span class="pl-new" data-new-key="guild.cubbies">New!</span></span>'
            in picker
        )
        assert picker.count('class="pl-new"') == 1
        assert 'aria-label="Guild Sections: Overview, section 1 of 3"' in picker

    def it_leaves_the_summary_dot_off_while_the_new_tab_is_the_current_one():
        row = BADGED.replace(' vote-tab--active">Overview', '">Overview').replace(
            'class="vote-tab"><span class="vote-tab__label">',
            'class="vote-tab vote-tab--active"><span class="vote-tab__label">',
        )
        picker = _render(row, '"Guild Sections"')[len(row) :]
        assert "data-tab-picker-dot" not in picker
        assert 'aria-label="Guild Sections: Cubbies, section 2 of 3"' in picker

    def it_hands_a_button_rows_badge_and_dot_to_the_mirror():
        html = _render(BADGED_BUTTONS, '"Guild Sections"')
        assert '<span class="pl-new-dot" aria-hidden="true" x-show="newElsewhere()" data-tab-picker-dot></span>' in html
        assert '<template x-if="item.new"><span class="pl-new">New!</span></template>' in html
        assert "data-new-key" not in html[len(BADGED_BUTTONS) :]
        # With no badge the dot waits under x-cloak for the mirror, so nothing flashes before Alpine runs.
        assert 'x-show="newElsewhere()" x-cloak data-tab-picker-dot' in _render(BUTTONS, '"Guild Sections"')

    def it_takes_the_eyebrow_from_a_variable():
        assert '<span class="pl-tab-picker__eyebrow">Manage Lathe</span>' in _render(LINKS, "name", name="Manage Lathe")

    def it_adds_nothing_to_a_row_with_one_tab():
        row = '<nav class="pl-tabs"><a href="/a/" class="vote-tab vote-tab--active">Only</a></nav>'
        assert _render(row) == row

    def it_takes_exactly_one_argument():
        with pytest.raises(TemplateSyntaxError, match="takes one argument"):
            Template("{% load tab_picker %}{% tab_picker %}{% endtab_picker %}")
