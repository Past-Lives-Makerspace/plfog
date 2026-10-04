"""BDD specs for core.html_sanitize — the rich-text email sanitizer + renderers."""

from __future__ import annotations

import time

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

    def it_drops_a_script_with_its_code():
        result = sanitize_rich_html("<p>Hello</p><script>alert(1)</script>")
        assert result == "<p>Hello</p>"

    def it_drops_a_style_block_with_its_css():
        # bleach keeps the text between stripped tags, so CSS used to show as words.
        result = sanitize_rich_html('<p>a</p><STYLE type="text/css">body{display:none}</STYLE >\n<p>b</p>')
        assert result == "<p>a</p>\n<p>b</p>"

    def it_drops_an_unclosed_style_to_the_end_as_a_browser_does():
        assert sanitize_rich_html("<p>keep</p><style>body{display:none}<p>gone</p>") == "<p>keep</p>"

    def it_stays_linear_on_thousands_of_unclosed_openers():
        # Patterns that rescanned to the end from every opener took seconds to a minute here.
        started = time.monotonic()
        sanitize_rich_html("<p>a</p>" + "<style>" * 64_000)
        sanitize_rich_html("<p>a</p>" + "<style " * 64_000)
        sanitize_rich_html("<p>a</p>" + "<ol>" * 32_000)
        sanitize_rich_html("<p>a</p>" + "<ol " * 32_000)
        for tag in ("ol", "blockquote", "b", "li"):
            sanitize_rich_html(f"<{tag}>" * 32_000)
        assert time.monotonic() - started < 3

    def it_keeps_the_text_of_tags_nested_past_the_cap():
        nested = "<blockquote>" * 100 + "deep" + "</blockquote>" * 100
        result = sanitize_rich_html("<p>top</p>" + nested)
        assert result.count("<blockquote>") == 64
        assert "deep" in result

    def it_leaves_ordinary_nesting_alone():
        raw = "<ul><li><strong><em><u>a</u></em></strong></li></ul><p>b<br>c</p>"
        assert sanitize_rich_html(raw) == raw

    def it_drops_a_style_block_and_nothing_else():
        assert sanitize_rich_html("<style>p{}</style>") == ""
        assert sanitize_rich_html("<p>keep</p><style>x</style><p>this</p><style>y</style>") == "<p>keep</p><p>this</p>"

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
    def it_styles_editor_html_for_the_light_card():
        raw = (
            "<h2>Welcome</h2><p>Bring <strong>tools</strong>.</p>"
            '<ul><li>Pencil</li></ul><p><a href="http://x.com">link</a></p>'
        )
        result = render_rich_email_body(raw)
        assert "margin:24px" in result  # h2 carries an inline style
        assert "font-weight:700" in result  # strong styled
        assert "padding-left:24px" in result  # list indented
        assert "color:#0d4876" in result  # link navy (white-background rebrand)
        assert 'target="_blank"' in result  # link hardened

    def it_paragraph_izes_legacy_plain_text():
        result = render_rich_email_body("First para.\n\nSecond line\nwrapped.")
        assert result.count("<p") == 2  # blank line → new paragraph
        assert "<br>" in result  # single newline → <br>
        assert "color:#33424F" in result  # slate body text styled for the light card

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


def describe_is_editor_html():
    def it_is_true_for_a_body_with_a_block_tag():
        from core.html_sanitize import is_editor_html

        assert is_editor_html("<p>Hi</p>") is True
        assert is_editor_html("one<br>two") is True

    def it_is_false_for_plain_text_even_with_angle_brackets():
        from core.html_sanitize import is_editor_html

        assert is_editor_html("Wear <closed toe shoes>.") is False
        assert is_editor_html("") is False


def describe_clean_rich_body():
    def it_sanitizes_editor_html():
        from core.html_sanitize import clean_rich_body

        out = clean_rich_body('<p onclick="x()">Hi <strong>there</strong></p><iframe src="x"></iframe>')
        assert out == "<p>Hi <strong>there</strong></p>"

    def it_stores_an_empty_editor_as_blank():
        from core.html_sanitize import clean_rich_body

        assert clean_rich_body("<p><br></p>") == ""
        assert clean_rich_body("   ") == ""

    def it_keeps_plain_text_exactly_as_typed_brackets_and_all():
        # Issue #425: a client without the editor posts text, and the renderer escapes it.
        from core.html_sanitize import clean_rich_body

        assert clean_rich_body("Wear <closed toe shoes> and bring <safety glasses>.") == (
            "Wear <closed toe shoes> and bring <safety glasses>."
        )


def describe_render_rich_body():
    def it_renders_editor_html_sanitized_and_unstyled():
        from core.html_sanitize import render_rich_body

        out = render_rich_body('<h2>Head</h2><p class="x">Body <em>x</em></p><iframe src="x"></iframe>')
        assert out == "<h2>Head</h2><p>Body <em>x</em></p>"
        assert "style=" not in out

    def it_renders_plain_text_as_escaped_paragraphs():
        from core.html_sanitize import render_rich_body

        out = render_rich_body("Wear <closed toe shoes>.\nBring glasses.\n\nTake it home.")
        assert out == "<p>Wear &lt;closed toe shoes&gt;.<br>Bring glasses.</p><p>Take it home.</p>"

    def it_is_blank_for_blank():
        from core.html_sanitize import render_rich_body

        assert render_rich_body("") == ""
        assert render_rich_body("  \n ") == ""


def describe_rich_body_to_text():
    def it_flattens_editor_html_to_lines_with_bullets():
        from core.html_sanitize import rich_body_to_text

        assert (
            rich_body_to_text("<p>Make a hook.</p><ul><li>one</li><li>two</li></ul>")
            == "Make a hook.\n\n- one\n\n- two"
        )

    def it_returns_plain_text_unchanged():
        from core.html_sanitize import rich_body_to_text

        assert rich_body_to_text("Line one\n\nLine <two>") == "Line one\n\nLine <two>"
