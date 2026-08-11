"""BDD specs for membership.markdown.render_markdown — full-markdown output stays sanitized.

These lock in the additive support for tables and deeper headings (Help page guides + guild
meeting notes) while proving the sanitizer still strips scripts, inline styles, and event
handlers. No database needed: render_markdown is a pure string transform.

The ``member`` profile is additionally pinned byte-for-byte to the golden fixtures in
``fixtures/markdown_golden/`` — rendered and committed BEFORE the ``profile=`` refactor,
so they are pre-refactor truth, not a comparison of the code against itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.utils.safestring import SafeString

from membership.markdown import (
    looks_like_html,
    render_markdown,
    render_page_content,
    sanitize_page_html,
    sanitize_page_submission,
)
from membership.templatetags.membership_md import help_markdown, page_content

_GOLDEN_DIR = Path(__file__).parent / "fixtures" / "markdown_golden"


def describe_render_markdown():
    def describe_tables():
        _TABLE = "| Guild | Cost |\n|-------|------|\n| Wood | $5 |\n| Metal | $8 |"

        def it_renders_a_markdown_table():
            html = render_markdown(_TABLE)
            assert "<table>" in html
            assert "<thead>" in html
            assert "<tbody>" in html

        def it_keeps_the_header_and_body_cells():
            html = render_markdown(_TABLE)
            assert "<th>Guild</th>" in html
            assert "<td>Metal</td>" in html

        def it_strips_the_style_based_column_alignment():
            # The tables extension emits alignment as inline style=, which the sanitizer drops.
            html = render_markdown("| a | b |\n|:--|--:|\n| 1 | 2 |")
            assert "<table>" in html
            assert "style=" not in html

    def describe_headings():
        def it_renders_an_h5():
            assert "<h5>Deep heading</h5>" in render_markdown("##### Deep heading")

        def it_renders_an_h6():
            assert "<h6>Deeper heading</h6>" in render_markdown("###### Deeper heading")

    def describe_sanitization_stays_strict():
        def it_strips_script_tags():
            html = render_markdown("<script>alert(1)</script>\n\nHello")
            assert "<script" not in html
            assert "alert(1)" not in html or "<script" not in html

        def it_strips_inline_styles():
            html = render_markdown('<p style="color:red">Styled</p>')
            assert "style=" not in html
            assert "Styled" in html

        def it_strips_event_handlers():
            html = render_markdown('<p onclick="steal()">Click</p>')
            assert "onclick" not in html
            assert "steal()" not in html

        def it_still_hardens_links():
            html = render_markdown("[docs](https://example.com)")
            assert 'rel="noopener nofollow noreferrer"' in html
            assert 'target="_blank"' in html

    def describe_profiles():
        def it_returns_empty_string_for_empty_source():
            assert render_markdown("") == ""
            assert render_markdown("", profile="help") == ""

        def it_raises_value_error_on_an_unknown_profile():
            with pytest.raises(ValueError, match="Unknown markdown profile 'guild'"):
                render_markdown("Hello", profile="guild")

        def it_raises_value_error_even_for_empty_source():
            # Fail loudly beats silently accepting a typoed profile name.
            with pytest.raises(ValueError):
                render_markdown("", profile="bogus")

    def describe_member_profile_golden_fixtures():
        def it_has_a_committed_fixture_corpus():
            assert len(sorted(_GOLDEN_DIR.glob("*.md"))) >= 8

        def it_matches_every_golden_fixture_byte_for_byte():
            for md_file in sorted(_GOLDEN_DIR.glob("*.md")):
                expected = md_file.with_suffix(".html").read_text()
                actual = render_markdown(md_file.read_text())
                assert actual == expected, f"member-profile output drifted for {md_file.name}"

    def describe_help_profile():
        def describe_images():
            def it_keeps_a_local_static_help_image_with_src_alt_and_title():
                html = render_markdown(
                    '<img src="/static/help/guild-voting/01-rank.png" alt="Rank" title="The card">',
                    profile="help",
                )
                assert 'src="/static/help/guild-voting/01-rank.png"' in html
                assert 'alt="Rank"' in html
                assert 'title="The card"' in html

            def it_keeps_a_markdown_syntax_image():
                html = render_markdown("![cap](/static/help/x/01.png)", profile="help")
                assert 'src="/static/help/x/01.png"' in html
                assert 'alt="cap"' in html

            def it_drops_attributes_outside_src_alt_title():
                html = render_markdown('<img src="/static/help/x/01.png" alt="a" width="600">', profile="help")
                assert "<img" in html
                assert "width" not in html

            def it_removes_an_external_image_entirely():
                html = render_markdown('<img src="https://evil.example.com/pixel.png" alt="t">', profile="help")
                assert "<img" not in html
                assert "evil.example.com" not in html

            def it_removes_a_data_uri_image_entirely():
                html = render_markdown('<img src="data:image/png;base64,AAAA" alt="t">', profile="help")
                assert "<img" not in html

            def it_removes_a_protocol_relative_image_entirely():
                html = render_markdown('<img src="//evil.example.com/pixel.png" alt="t">', profile="help")
                assert "<img" not in html

            def it_removes_a_media_upload_image_entirely():
                html = render_markdown('<img src="/media/uploads/p.png" alt="t">', profile="help")
                assert "<img" not in html

            def it_removes_a_srcless_image_entirely():
                html = render_markdown('<img alt="just alt">', profile="help")
                assert "<img" not in html

            def it_still_strips_images_in_the_member_profile():
                html = render_markdown('<img src="/static/help/x/01.png" alt="a">')
                assert "<img" not in html

        def describe_heading_anchors():
            def it_keeps_a_pattern_valid_id_on_h2_h3_h4():
                for level, marker in ((2, "##"), (3, "###"), (4, "####")):
                    html = render_markdown(f"{marker} Rank your top 3 {{#voting-rank-guilds}}", profile="help")
                    assert f'<h{level} id="voting-rank-guilds">' in html

            def it_rejects_an_uppercase_id():
                html = render_markdown("## Head {#Voting-Rank}", profile="help")
                assert "id=" not in html

            def it_rejects_an_underscore_id():
                html = render_markdown("## Head {#voting_rank}", profile="help")
                assert "id=" not in html

            def it_rejects_an_id_longer_than_80_chars():
                html = render_markdown("## Head {#" + "a" * 81 + "}", profile="help")
                assert "id=" not in html

            def it_rejects_an_id_on_headings_outside_h2_h3_h4():
                html = render_markdown("# Head {#top-anchor}", profile="help")
                assert "id=" not in html

            def it_drops_other_attr_list_attributes_like_class():
                html = render_markdown("## Head {#ok-id .fancy}", profile="help")
                assert 'id="ok-id"' in html
                assert "class=" not in html

        def describe_links():
            def it_keeps_internal_links_same_tab_with_rel_noopener():
                html = render_markdown("[vote](/guilds/voting/)", profile="help")
                assert '<a href="/guilds/voting/" rel="noopener">vote</a>' in html
                assert "target=" not in html
                assert "nofollow" not in html

            def it_keeps_hash_anchor_links_same_tab():
                html = render_markdown("[faq](#faq)", profile="help")
                assert '<a href="#faq" rel="noopener">faq</a>' in html
                assert "target=" not in html

            def it_fully_hardens_external_links():
                html = render_markdown("[docs](https://example.com)", profile="help")
                assert 'rel="noopener nofollow noreferrer"' in html
                assert 'target="_blank"' in html

            def it_hardens_a_link_whose_unsafe_href_was_stripped():
                # bleach drops the javascript: href; the hrefless anchor is treated as external.
                html = render_markdown('<a href="javascript:alert(1)">Bad</a>', profile="help")
                assert "javascript:" not in html
                assert 'rel="noopener nofollow noreferrer"' in html

        def describe_admonitions():
            def it_renders_a_tip_callout_with_its_title_row():
                html = render_markdown('!!! tip "Stuck?"\n    Check Who\'s Who below.', profile="help")
                assert '<div class="admonition tip">' in html
                assert '<p class="admonition-title">Stuck?</p>' in html
                assert "Check Who's Who below." in html

            def it_renders_every_allowed_callout_type():
                for kind in ("note", "info", "tip", "warning"):
                    html = render_markdown(f"!!! {kind}\n    Body.", profile="help")
                    assert f'<div class="admonition {kind}">' in html

            def it_strips_the_class_from_an_unknown_callout_type():
                # `!!! danger` emits class="admonition danger" — "danger" is outside the
                # allowlist, so the whole div class is dropped (the content survives).
                html = render_markdown("!!! danger\n    Boom.", profile="help")
                assert "danger" not in html
                assert "Boom." in html

            def it_strips_a_smuggled_div_class_entirely():
                html = render_markdown('<div class="admonition pl-modal">hi</div>', profile="help")
                assert "pl-modal" not in html
                assert "class=" not in html

            def it_strips_a_free_class_on_a_paragraph():
                html = render_markdown('<p class="pl-hax">hi</p>', profile="help")
                assert "class=" not in html
                assert "hi" in html

            def it_keeps_a_hand_written_admonition_title_class_on_a_paragraph():
                html = render_markdown('<p class="admonition-title">Note</p>', profile="help")
                assert '<p class="admonition-title">Note</p>' in html

            def it_leaves_the_member_profile_without_admonitions():
                # The member profile has no admonition extension and no div allowlist —
                # the `!!!` block stays literal text and no div survives.
                html = render_markdown("!!! tip\n    Stuck?")
                assert "admonition" not in html
                assert "<div" not in html
                assert "!!! tip" in html

            def it_strips_a_raw_div_in_the_member_profile():
                html = render_markdown('<div class="admonition note">hi</div>')
                assert "<div" not in html
                assert "hi" in html

        def describe_sanitization_stays_strict():
            def it_strips_script_tags():
                html = render_markdown("<script>alert(1)</script>\n\nHello", profile="help")
                assert "<script" not in html

            def it_strips_style_tags_and_inline_styles():
                html = render_markdown('<style>body{}</style>\n\n<p style="color:red">Styled</p>', profile="help")
                assert "<style" not in html
                assert "style=" not in html
                assert "Styled" in html

            def it_strips_event_handlers():
                html = render_markdown('<p onclick="steal()">Click</p>', profile="help")
                assert "onclick" not in html


def describe_help_markdown_filter():
    def it_renders_through_the_help_profile():
        html = help_markdown("![cap](/static/help/x/01.png)\n\n[vote](/guilds/voting/)")
        assert 'src="/static/help/x/01.png"' in html
        assert '<a href="/guilds/voting/" rel="noopener">vote</a>' in html

    def it_returns_a_safestring():
        assert isinstance(help_markdown("Hello"), SafeString)

    def it_renders_none_as_empty():
        assert help_markdown(None) == ""  # type: ignore[arg-type]


def describe_looks_like_html():
    def it_is_true_for_a_block_tag():
        assert looks_like_html("<p>Hello</p>") is True

    def it_is_true_with_leading_whitespace_and_newlines():
        assert looks_like_html("  \n\t<h2>Parking</h2>") is True

    def it_is_false_for_markdown():
        assert looks_like_html("## Parking\n\n**Free** after 5pm.") is False

    def it_is_false_for_markdown_with_embedded_inline_html():
        assert looks_like_html("Some text with <em>inline</em> HTML.") is False

    def it_is_false_for_empty_source():
        assert looks_like_html("") is False


def describe_sanitize_page_html():
    def describe_the_allowlist():
        def it_keeps_every_tag_the_quill_toolbar_can_produce():
            source = (
                "<h2>Head</h2><h3>Sub</h3><p><strong>b</strong> <em>i</em> <u>u</u> <s>s</s></p>"
                "<ol><li>one</li></ol><ul><li>two</li></ul><blockquote>quoted</blockquote><p>a<br>b</p>"
            )
            html = sanitize_page_html(source)
            for tag in ("<h2>", "<h3>", "<strong>", "<em>", "<u>", "<s>", "<ol>", "<ul>", "<li>", "<blockquote>"):
                assert tag in html
            assert "<br" in html

        def it_strips_script_but_keeps_surrounding_content():
            html = sanitize_page_html("<p>Hello</p><script>alert(1)</script>")
            assert "<script" not in html
            assert "Hello" in html

        def it_strips_style_tags_iframes_and_event_handlers():
            html = sanitize_page_html('<style>body{}</style><iframe src="x"></iframe><p onclick="evil()">Hi</p>')
            assert "<style" not in html
            assert "<iframe" not in html
            assert "onclick" not in html
            assert "Hi" in html

        def it_strips_inline_style_and_quill_class_attributes():
            html = sanitize_page_html('<p style="color:red" class="ql-align-center">x</p>')
            assert "style=" not in html
            assert "class=" not in html
            assert "x" in html

        def it_strips_images_entirely():
            html = sanitize_page_html('<p>before</p><img src="/static/help/x/01.png" alt="cap"><p>after</p>')
            assert "<img" not in html
            assert "before" in html and "after" in html

        def it_strips_h1_and_tables_keeping_their_text():
            html = sanitize_page_html("<h1>Big</h1><table><tr><td>cell</td></tr></table>")
            assert "<h1" not in html
            assert "<table" not in html
            assert "Big" in html
            assert "cell" in html

    def describe_quill_list_normalization():
        def it_rewrites_a_quill_bullet_ol_to_a_semantic_ul():
            html = sanitize_page_html('<ol><li data-list="bullet">a</li><li data-list="bullet">b</li></ol>')
            assert "<ul>" in html
            assert "<ol" not in html

        def it_keeps_a_real_ordered_list_as_ol():
            html = sanitize_page_html('<ol><li data-list="ordered">a</li></ol>')
            assert "<ol" in html
            assert "<ul>" not in html

    def describe_links():
        def it_keeps_internal_links_same_tab_with_rel_noopener():
            html = sanitize_page_html('<p><a href="/guilds/voting/">vote</a></p>')
            assert '<a href="/guilds/voting/" rel="noopener">vote</a>' in html

        def it_keeps_hash_anchor_links_same_tab():
            html = sanitize_page_html('<p><a href="#parking">jump</a></p>')
            assert '<a href="#parking" rel="noopener">jump</a>' in html

        def it_fully_hardens_external_links():
            html = sanitize_page_html('<p><a href="https://example.com">out</a></p>')
            assert 'rel="noopener nofollow noreferrer"' in html
            assert 'target="_blank"' in html

        def it_strips_a_javascript_href_and_hardens_the_leftover_anchor():
            html = sanitize_page_html('<p><a href="javascript:alert(1)">x</a></p>')
            assert "javascript:" not in html

    def describe_fail_closed_emptiness():
        def it_returns_empty_for_empty_input():
            assert sanitize_page_html("") == ""

        def it_returns_empty_for_whitespace():
            assert sanitize_page_html("   \n ") == ""

        def it_returns_empty_for_an_empty_quill_document():
            assert sanitize_page_html("<p><br></p>") == ""

        def it_returns_empty_when_nothing_survives_sanitization():
            assert sanitize_page_html("<img src='https://evil.example/x.png'>") == ""


def describe_render_page_content():
    def it_routes_markdown_through_render_markdown_unchanged():
        source = "## Parking\n\n**Free** after 5pm."
        assert render_page_content(source) == render_markdown(source, profile="help")

    def it_honors_the_markdown_profile_argument():
        source = "**Free** parking"
        assert render_page_content(source, profile="member") == render_markdown(source, profile="member")

    def it_routes_html_through_the_page_sanitizer():
        source = '<p onclick="evil()">Hi <strong>there</strong></p>'
        assert render_page_content(source) == sanitize_page_html(source)

    def it_sniffs_html_despite_leading_whitespace():
        # Routed through the sanitizer, the **…** stays literal; the Markdown path would bold it.
        html = render_page_content("  \n<p>**not markdown**</p>")
        assert "**not markdown**" in html
        assert "<strong>" not in html

    def it_returns_empty_for_empty_source():
        assert render_page_content("") == ""


def describe_sanitize_page_submission():
    def it_passes_markdown_through_byte_for_byte():
        source = "## Parking\n\n**Free** after 5pm — see [map](/help/)."
        assert sanitize_page_submission(source) == source

    def it_sanitizes_editor_html():
        assert sanitize_page_submission("<p class='ql-x' onclick='evil()'>Hi</p>") == "<p>Hi</p>"

    def it_returns_empty_for_a_contentless_editor_document():
        assert sanitize_page_submission("<p><br></p>") == ""


def describe_page_content_filter():
    def it_renders_html_content_sanitized():
        html = page_content("<p><strong>Bold</strong> <script>alert(1)</script>plan</p>")
        assert "<strong>Bold</strong>" in html
        assert "<script" not in html

    def it_renders_markdown_content_through_the_given_profile():
        assert page_content("**bold**", "member") == render_markdown("**bold**", profile="member")

    def it_defaults_to_the_help_profile_for_markdown():
        source = "![cap](/static/help/x/01.png)"
        assert page_content(source) == render_markdown(source, profile="help")

    def it_returns_a_safestring():
        assert isinstance(page_content("<p>Hello</p>"), SafeString)

    def it_renders_none_as_empty():
        assert page_content(None) == ""  # type: ignore[arg-type]
