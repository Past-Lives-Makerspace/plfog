"""BDD specs for membership.markdown.render_markdown — full-markdown output stays sanitized.

These lock in the additive support for tables and deeper headings (Help page guides + guild
meeting notes) while proving the sanitizer still strips scripts, inline styles, and event
handlers. No database needed: render_markdown is a pure string transform.
"""

from __future__ import annotations

from membership.markdown import render_markdown


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
