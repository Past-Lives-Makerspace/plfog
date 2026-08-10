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

from membership.markdown import render_markdown
from membership.templatetags.membership_md import help_markdown

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
