"""BDD specs for core.html_sanitize — the rich-text email sanitizer + renderers."""

from __future__ import annotations

from core.html_sanitize import (
    render_rich_email_body,
    render_rich_email_text,
    rich_html_to_text,
    sanitize_rich_html,
)


def describe_sanitize_rich_html():
    def it_keeps_each_allowed_tag():
        raw = "<h2>Head</h2><h3>Sub</h3><p>A <strong>b</strong> <em>c</em> <u>d</u></p><blockquote>q</blockquote>"
        result = sanitize_rich_html(raw)
        for tag in ("<h2>", "<h3>", "<p>", "<strong>", "<em>", "<u>", "<blockquote>"):
            assert tag in result

    def it_strips_script_but_keeps_inner_text():
        result = sanitize_rich_html("<p>Hello</p><script>alert(1)</script>")
        assert "<script" not in result
        assert "Hello" in result

    def it_strips_style_tags_iframes_and_event_handlers():
        result = sanitize_rich_html('<style>body{}</style><iframe src="x"></iframe><p onclick="evil()">Hi</p>')
        assert "<style" not in result
        assert "<iframe" not in result
        assert "onclick" not in result
        assert "Hi" in result

    def it_strips_unknown_tags_but_keeps_their_text():
        result = sanitize_rich_html("<marquee>scrolling</marquee>")
        assert "<marquee" not in result
        assert "scrolling" in result

    def it_strips_inline_style_and_class_attributes():
        result = sanitize_rich_html('<p style="color:red" class="evil">x</p>')
        assert "style=" not in result
        assert "class=" not in result
        assert "x" in result

    def it_hardens_links():
        result = sanitize_rich_html('<p><a href="http://example.com">link</a></p>')
        assert 'href="http://example.com"' in result
        assert 'rel="noopener nofollow noreferrer"' in result
        assert 'target="_blank"' in result

    def it_normalizes_quill_bullet_lists_to_a_real_ul():
        raw = '<ol><li data-list="bullet">A</li><li data-list="bullet">B</li></ol>'
        result = sanitize_rich_html(raw)
        assert "<ul>" in result
        assert "<ol>" not in result
        assert "data-list" not in result

    def it_leaves_ordered_lists_as_ol():
        raw = '<ol><li data-list="ordered">A</li></ol>'
        result = sanitize_rich_html(raw)
        assert "<ol>" in result
        assert "<ul>" not in result

    def it_returns_empty_for_blank_input():
        assert sanitize_rich_html("") == ""
        assert sanitize_rich_html("   ") == ""

    def it_returns_empty_for_an_empty_quill_doc():
        assert sanitize_rich_html("<p><br></p>") == ""


def describe_rich_html_to_text():
    def it_flattens_tags_unescapes_entities_and_collapses_whitespace():
        html = "<h2>Title</h2><p>One &amp; two</p><ul><li>a</li><li>b</li></ul>"
        text = rich_html_to_text(html)
        assert "<" not in text
        assert "One & two" in text
        assert "  " not in text  # whitespace collapsed to single spaces

    def it_returns_empty_for_empty_input():
        assert rich_html_to_text("") == ""


def describe_render_rich_email_body():
    def it_styles_editor_html_for_the_dark_card():
        raw = (
            "<h2>Welcome</h2><p>Bring <strong>tools</strong>.</p>"
            '<ul><li>Pencil</li></ul><p><a href="http://x.com">link</a></p>'
        )
        result = render_rich_email_body(raw)
        assert "margin:24px" in result  # h2 carries an inline style
        assert "font-weight:700" in result  # strong styled
        assert "padding-left:24px" in result  # list indented
        assert "color:#EEB44B" in result  # link gold
        assert 'target="_blank"' in result  # link hardened

    def it_paragraph_izes_legacy_plain_text():
        result = render_rich_email_body("First para.\n\nSecond line\nwrapped.")
        assert result.count("<p") == 2  # blank line → new paragraph
        assert "<br>" in result  # single newline → <br>
        assert "color:#F4EFDD" in result  # styled for the dark card

    def it_returns_empty_for_blank_input():
        assert render_rich_email_body("") == ""
        assert render_rich_email_body("   ") == ""

    def it_returns_empty_for_an_empty_quill_doc():
        assert render_rich_email_body("<p><br></p>") == ""


def describe_render_rich_email_text():
    def it_returns_legacy_plain_text_unchanged():
        assert render_rich_email_text("Line one\n\nLine two") == "Line one\n\nLine two"

    def it_flattens_editor_html_to_readable_lines_with_bullets():
        result = render_rich_email_text("<h2>Hi</h2><p>Body</p><ul><li>one</li><li>two</li></ul>")
        assert "<" not in result
        assert "Hi" in result
        assert "- one" in result
        assert "- two" in result

    def it_returns_empty_for_blank_input():
        assert render_rich_email_text("") == ""

    def it_returns_empty_for_an_empty_quill_doc():
        assert render_rich_email_text("<p><br></p>") == ""
