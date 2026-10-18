"""BDD specs for the ``wiki`` sanitizer profile and the wiki render path.

The constraint this file defends: the wiki profile is a NEW third profile, and adding it
must not loosen the shared ``member`` profile or the admin-authored ``help`` one. The
golden-fixture spec in ``markdown_spec.py`` pins ``render_markdown(source)`` byte for
byte and stays untouched; here we pin what the new profile does and, just as important,
what it refuses.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from membership.markdown import (
    WIKI_PANEL_KINDS,
    _inject_heading_ids,
    render_markdown,
    render_wiki_content,
    sanitize_page_submission,
    sanitize_wiki_html,
    sanitize_wiki_submission,
    wiki_image_src_prefixes,
)


def describe_the_wiki_profile_guard():
    def it_accepts_the_new_profile():
        assert render_markdown("Hello", profile="wiki") == "<p>Hello</p>"

    def it_still_rejects_an_unknown_profile():
        with pytest.raises(ValueError, match="Unknown markdown profile 'bogus'"):
            render_markdown("x", profile="bogus")

    def it_rejects_an_unknown_profile_even_for_empty_source():
        with pytest.raises(ValueError):
            render_markdown("", profile="guild")

    def it_returns_empty_for_empty_source():
        assert render_markdown("", profile="wiki") == ""


def describe_wiki_image_src_prefixes():
    def it_allows_the_local_media_prefix():
        assert "/media/wiki/" in wiki_image_src_prefixes()

    @override_settings(R2_PUBLIC_URL="https://cdn.example.com")
    def it_adds_the_r2_prefix_when_configured():
        assert "https://cdn.example.com/wiki/" in wiki_image_src_prefixes()

    @override_settings(R2_PUBLIC_URL="")
    def it_omits_the_r2_prefix_when_unconfigured():
        assert wiki_image_src_prefixes() == ("/media/wiki/",)


def describe_wiki_profile_images():
    def it_keeps_an_image_from_the_wiki_media_prefix():
        html = render_markdown('<img src="/media/wiki/saw.png" alt="Saw">', profile="wiki")
        assert 'src="/media/wiki/saw.png"' in html
        assert 'alt="Saw"' in html

    def it_drops_an_image_from_another_local_prefix():
        html = render_markdown('<img src="/media/uploads/p.png" alt="t">', profile="wiki")
        assert "<img" not in html

    def it_drops_an_absolute_external_image():
        html = render_markdown('<img src="https://evil.example.com/pixel.png" alt="t">', profile="wiki")
        assert "<img" not in html

    def it_drops_a_protocol_relative_image():
        html = render_markdown('<img src="//evil.example.com/pixel.png" alt="t">', profile="wiki")
        assert "<img" not in html

    def it_drops_a_data_uri_image():
        html = render_markdown('<img src="data:image/png;base64,AAAA" alt="t">', profile="wiki")
        assert "<img" not in html

    def it_removes_a_src_less_image_entirely():
        # A rejected source leaves a useless tag behind; the page should not render it.
        html = render_markdown('<img alt="just alt">', profile="wiki")
        assert "<img" not in html


def describe_wiki_profile_isolation():
    def it_strips_an_iframe_the_help_profile_allows():
        embed = '<iframe src="https://www.loom.com/embed/abc123" title="Vid"></iframe>'
        assert "<iframe" in render_markdown(embed, profile="help")
        assert "<iframe" not in render_markdown(embed, profile="wiki")

    def it_strips_a_script():
        assert "<script" not in render_markdown("<script>alert(1)</script>", profile="wiki")

    def it_strips_an_inline_style_attribute():
        html = render_markdown('<p style="color:red">Red</p>', profile="wiki")
        assert "style=" not in html

    def it_strips_an_event_handler():
        html = render_markdown('<p onclick="steal()">Click</p>', profile="wiki")
        assert "onclick" not in html

    def it_strips_an_unknown_tag_but_keeps_its_text():
        html = render_markdown("<marquee>Scrolling</marquee>", profile="wiki")
        assert "marquee" not in html
        assert "Scrolling" in html

    def it_does_not_loosen_the_member_profile():
        # The member profile has never allowed images and must not start now.
        html = render_markdown('<img src="/media/wiki/saw.png" alt="Saw">')
        assert "<img" not in html


def describe_sanitize_wiki_html():
    def it_returns_empty_for_blank_input():
        assert sanitize_wiki_html("") == ""
        assert sanitize_wiki_html("   ") == ""

    def it_returns_empty_for_an_empty_quill_body():
        assert sanitize_wiki_html("<p><br></p>") == ""

    def it_keeps_a_body_that_is_only_a_photo():
        # The phone contribution path: a member with gloves and thirty seconds adds a
        # picture of the right blade and no words at all. That is not a blank save.
        html = sanitize_wiki_html('<p><img src="/media/wiki/blade.png" alt="Blade"></p>')
        assert 'src="/media/wiki/blade.png"' in html

    def it_keeps_a_table_a_member_pasted():
        html = sanitize_wiki_html("<table><tbody><tr><td>Walnut</td></tr></tbody></table>")
        assert "<table>" in html
        assert "Walnut" in html

    def it_keeps_a_wiki_image():
        html = sanitize_wiki_html('<p><img src="/media/wiki/blade.png" alt="Blade"></p>')
        assert 'src="/media/wiki/blade.png"' in html

    def it_drops_a_foreign_image():
        html = sanitize_wiki_html('<p><img src="https://evil.example.com/x.png" alt="x"></p>')
        assert "<img" not in html

    def it_strips_an_iframe():
        assert "<iframe" not in sanitize_wiki_html('<p><iframe src="https://loom.com/e/1"></iframe></p>')


def describe_sanitize_wiki_submission():
    def it_sanitizes_editor_html():
        assert "<script" not in sanitize_wiki_submission("<p>Hi<script>alert(1)</script></p>")

    def it_passes_markdown_through_unchanged():
        source = "## Setup\n\nUse a **sharp** blade."
        assert sanitize_wiki_submission(source) == source

    def it_leaves_the_page_submission_sanitizer_alone():
        # The two profiles must stay independently editable, in both directions: the
        # wiki keeps a pasted table the page sanitizer drops, and neither one is
        # implemented by calling the other.
        table = "<table><tbody><tr><td>Walnut</td></tr></tbody></table>"
        assert "<table>" in sanitize_wiki_submission(table)
        assert "<table>" not in sanitize_page_submission(table)


def describe_render_wiki_content():
    def it_returns_empty_for_empty_source():
        assert render_wiki_content("") == ""

    def it_renders_markdown_through_the_wiki_profile():
        html = render_wiki_content("## Setup\n\nUse a sharp blade.")
        assert "<h2" in html
        assert "sharp blade" in html

    def it_renders_quill_html_through_the_html_sanitizer():
        html = render_wiki_content("<h2>Setup</h2><p>Use a sharp blade.</p>")
        assert "<h2" in html
        assert "sharp blade" in html

    def describe_heading_ids():
        def it_slugifies_the_heading_text():
            assert 'id="blade-changes"' in render_wiki_content("<h2>Blade changes</h2>")

        def it_dedupes_repeated_heading_text():
            html = render_wiki_content("<h2>Setup</h2><h3>Setup</h3>")
            assert 'id="setup"' in html
            assert 'id="setup-2"' in html

        def it_preserves_an_existing_valid_id():
            # Exercised directly: the Quill allowlist drops a heading id before this
            # runs, so the preserve branch is only reachable from the Markdown path.
            html = _inject_heading_ids('<h2 id="kept">Renamed heading</h2>')
            assert 'id="kept"' in html
            assert 'id="renamed-heading"' not in html

        def it_replaces_an_invalid_existing_id():
            html = _inject_heading_ids('<h2 id="Not A Slug">Blade changes</h2>')
            assert 'id="blade-changes"' in html
            # The stale id must be GONE, not merely joined by a second one: a browser
            # honors the first id and the TOC regex captures the last, so a duplicate
            # sends the chip to an anchor that does not exist.
            assert "Not A Slug" not in html
            assert html.count("id=") == 1

        def it_does_not_let_a_later_heading_collide_with_a_preserved_id():
            html = _inject_heading_ids('<h2 id="setup">First</h2><h3>Setup</h3>')
            assert 'id="setup"' in html
            assert 'id="setup-2"' in html

        def it_falls_back_for_a_heading_with_no_text():
            assert 'id="section"' in render_wiki_content("<h2><img src='/media/wiki/x.png'></h2>")

        def it_is_deterministic_across_renders():
            source = "<h2>Setup</h2><h3>Setup</h3><h2>Cleanup</h2>"
            assert render_wiki_content(source) == render_wiki_content(source)


def describe_wiki_blocks():
    """The editor's Confluence-style blocks: panels, checklist, settings block, divider."""

    def describe_panels():
        @pytest.mark.parametrize("kind", WIKI_PANEL_KINDS)
        def it_keeps_each_panel_class_on_a_line(kind):
            html = sanitize_wiki_html(f'<p class="pl-panel-{kind}">Wear eye protection.</p>')
            assert html == f'<p class="pl-panel-{kind}">Wear eye protection.</p>'

        @pytest.mark.parametrize("tag", ["h2", "h3", "h4"])
        def it_keeps_a_panel_class_on_a_heading(tag):
            assert f'<{tag} class="pl-panel-tip">' in sanitize_wiki_html(f'<{tag} class="pl-panel-tip">Tip</{tag}>')

        @pytest.mark.parametrize(
            "value", ["pl-panel-evil", "ql-align-center", "pl-panel-info ql-align-center", "pl-checklist"]
        )
        def it_drops_any_other_class(value):
            assert sanitize_wiki_html(f'<p class="{value}">Text</p>') == "<p>Text</p>"

        def it_drops_other_attributes_on_a_panel_line():
            assert sanitize_wiki_html('<p class="pl-panel-info" onclick="x()">Text</p>') == (
                '<p class="pl-panel-info">Text</p>'
            )

        def it_drops_an_empty_panel_line_on_the_reading_page():
            source = '<h2>Danger</h2><p class="pl-panel-danger"><br></p><p class="pl-panel-tip">Kept</p>'
            html = render_wiki_content(source)
            assert "pl-panel-danger" not in html
            assert '<p class="pl-panel-tip">Kept</p>' in html

        def it_keeps_an_empty_panel_line_for_the_editor():
            html = render_wiki_content('<h2>Danger</h2><p class="pl-panel-danger"><br></p>', for_editor=True)
            assert '<p class="pl-panel-danger"><br></p>' in html

        def it_keeps_an_empty_paragraph_that_is_not_a_panel():
            assert "<p><br></p>" in render_wiki_content("<h2>A</h2><p><br></p>")

    def describe_checklists():
        QUILL = (
            '<ol><li data-list="checked">Sweep<span class="ql-ui"></span></li><li data-list="unchecked">Lock</li></ol>'
        )

        def it_turns_a_quill_checklist_into_a_marked_list():
            assert sanitize_wiki_html(QUILL) == (
                '<ul class="pl-checklist"><li data-list="checked">Sweep</li><li data-list="unchecked">Lock</li></ul>'
            )

        def it_is_stable_on_a_second_pass():
            once = sanitize_wiki_html(QUILL)
            assert sanitize_wiki_html(once) == once

        def it_leaves_bullet_and_numbered_lists_as_they_were():
            assert sanitize_wiki_html('<ol><li data-list="bullet">A</li></ol>') == "<ul><li>A</li></ul>"
            assert sanitize_wiki_html('<ol><li data-list="ordered">A</li></ol>') == "<ol><li>A</li></ol>"

        @pytest.mark.parametrize("value", ["bullet", "ordered", "evil"])
        def it_drops_any_other_data_list_value(value):
            assert "data-list" not in sanitize_wiki_html(
                f'<ul class="pl-checklist"><li data-list="{value}">A</li></ul>'
            )

        def it_drops_an_empty_unticked_item_and_then_the_empty_list_on_the_reading_page():
            html = render_wiki_content(
                '<h2>Before</h2><ul class="pl-checklist"><li data-list="unchecked"><br></li></ul>'
            )
            assert "pl-checklist" not in html
            assert "<li" not in html

        def it_keeps_written_items_beside_an_empty_one():
            source = (
                '<ul class="pl-checklist"><li data-list="unchecked">Sweep</li><li data-list="unchecked"><br></li></ul>'
            )
            assert render_wiki_content(source) == '<ul class="pl-checklist"><li data-list="unchecked">Sweep</li></ul>'

        def it_keeps_empty_items_for_the_editor():
            source = '<h2>A</h2><ul class="pl-checklist"><li data-list="unchecked"><br></li></ul>'
            assert '<li data-list="unchecked"><br></li>' in render_wiki_content(source, for_editor=True)

    def describe_settings_blocks():
        QUILL = (
            '<div class="ql-code-block-container" spellcheck="false">'
            '<div class="ql-code-block" data-language="plain">Power 80%</div>'
            '<div class="ql-code-block" data-language="plain"><br></div>'
            '<div class="ql-code-block" data-language="plain">Speed &lt;20&gt;</div>'
            "</div>"
        )

        def it_turns_quills_line_divs_into_one_pre_with_a_line_each():
            assert sanitize_wiki_html(QUILL) == "<pre>Power 80%\n\nSpeed &lt;20&gt;</pre>"

        def it_is_stable_on_a_second_pass():
            once = sanitize_wiki_html(QUILL)
            assert sanitize_wiki_html(once) == once

        def it_leaves_a_stray_div_to_the_sanitizer():
            assert sanitize_wiki_html('<div class="ql-code-block">x</div>') == "x"

    def describe_dividers():
        def it_keeps_a_divider():
            assert sanitize_wiki_html("<p>A</p><hr><p>B</p>") == "<p>A</p><hr><p>B</p>"
