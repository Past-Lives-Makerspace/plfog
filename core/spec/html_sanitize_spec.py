"""BDD specs for core.html_sanitize — the rich-text email sanitizer + renderers."""

from __future__ import annotations

import time

import pytest

from core.html_sanitize import (
    _RAW_TEXT_ELEMENT_RE,
    _normalize_quill_lists,
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
        # Stripping keeps the text between stripped tags, so CSS used to show as words.
        result = sanitize_rich_html('<p>a</p><STYLE type="text/css">body{display:none}</STYLE >\n<p>b</p>')
        assert result == "<p>a</p>\n<p>b</p>"

    def it_drops_an_unclosed_style_to_the_end_as_a_browser_does():
        assert sanitize_rich_html("<p>keep</p><style>body{display:none}<p>gone</p>") == "<p>keep</p>"

    def it_scans_unclosed_openers_in_linear_time():
        # The two pre-passes once rescanned to the end from every unclosed opener: 112 KB of
        # <style> took about 4 seconds, 125 KB of <ol> most of a minute.
        started = time.monotonic()
        for raw in ("<style>" * 64_000, "<style " * 64_000, "<script><style>" * 32_000):
            _RAW_TEXT_ELEMENT_RE.sub("", raw)
        for raw in ("<ol>" * 64_000, "<ol " * 64_000):
            _normalize_quill_lists(raw)
        assert time.monotonic() - started < 1

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


def describe_stripping_tags_outside_the_allowlist():
    # Each expectation here is also what bleach produced for the same input.
    def it_leaves_a_line_break_where_a_block_tag_was_stripped():
        assert rich_html_to_text("<div>1</div><div>Class</div>") == "1 Class"
        assert sanitize_rich_html("<p>a</p><div>b</div>") == "<p>a</p>\nb"

    def it_leaves_no_line_break_for_the_very_first_tag():
        from core.html_sanitize import AllowlistCleaner

        assert AllowlistCleaner((), {}).clean("<div>a</div>") == "a"

    def it_does_not_let_a_stripped_block_tag_split_a_paragraph():
        assert sanitize_rich_html("<p>a<div>b</div>c</p>") == "<p>a\nbc</p>"

    def it_keeps_a_less_than_sign_that_starts_no_tag_as_text():
        assert sanitize_rich_html("<p>1 &lt; 2 and 3 < 4 <<b>x</b></p>") == "<p>1 &lt; 2 and 3 &lt; 4 &lt;<b>x</b></p>"

    def it_never_joins_text_around_a_stripped_tag_into_a_new_tag():
        out = sanitize_rich_html("<p><<div>script>alert(1)<</div>/script></p>")
        assert out == "<p>&lt;\nscript&gt;alert(1)&lt;/script&gt;</p>"

    def it_drops_comments_of_every_shape():
        assert sanitize_rich_html("<p><!-- note -->a<!---->b<!-->c<!--->d<!x>e<?x>f</p>") == "<p>abcdef</p>"

    def it_reads_a_greater_than_sign_inside_a_quoted_value_as_part_of_the_value():
        out = sanitize_rich_html("<p><a title=\"a>b\" href='/x'>t</a></p>")
        assert out.startswith('<p><a title="a&gt;b" href="/x" ')

    def it_strips_a_tag_with_an_unquoted_attribute():
        assert sanitize_rich_html("<p><span x=y>z</span></p>") == "<p>z</p>"

    def it_drops_a_tag_cut_off_by_the_end_of_the_input():
        assert sanitize_rich_html("<p>hello</p><stro") == "<p>hello</p>"

    def it_reads_tag_names_in_any_case():
        assert sanitize_rich_html("<P>Up<BR>per</P>") == "<p>Up<br>per</p>"


def describe_allowlist_cleaner():
    def it_keeps_an_attribute_only_when_its_filter_accepts_it():
        from core.html_sanitize import AllowlistCleaner

        cleaner = AllowlistCleaner(
            ["p", "img"],
            {"img": {"src", "alt"}},
            {"img": lambda tag, name, value: name != "src" or value.startswith("/ok/")},
        )
        out = cleaner.clean('<p title="t"><img src="/ok/a.png" alt="a"><img src="/no/b.png" alt="b"></p>')
        assert out == '<p><img src="/ok/a.png" alt="a"><img alt="b"></p>'

    def it_keeps_tel_links_and_drops_script_links():
        out = sanitize_rich_html('<p><a href="tel:+15035550199">call</a> <a href="javascript:alert(1)">x</a></p>')
        assert 'href="tel:+15035550199"' in out
        assert "javascript" not in out


def describe_the_rich_text_size_limit():
    def it_passes_text_at_the_limit_through_unchanged():
        from core.html_sanitize import RICH_TEXT_MAX_CHARS, limit_rich_text

        value = "x" * RICH_TEXT_MAX_CHARS
        assert limit_rich_text(value) is value

    def it_refuses_text_over_the_limit_with_plain_copy():
        from django.core.exceptions import ValidationError

        from core.html_sanitize import RICH_TEXT_MAX_CHARS, RichTextTooLongError, limit_rich_text

        with pytest.raises(RichTextTooLongError) as caught:
            limit_rich_text("x" * (RICH_TEXT_MAX_CHARS + 1))
        assert str(caught.value) == "This is too long to save. Shorten it to under 100,000 characters."
        assert isinstance(caught.value, ValidationError)
        assert isinstance(caught.value, ValueError)
        assert caught.value.messages == [str(caught.value)]
        assert caught.value.code == "too_long"

    def it_applies_to_editor_html_before_sanitizing():
        from core.html_sanitize import RICH_TEXT_MAX_CHARS, RichTextTooLongError, clean_rich_html

        assert clean_rich_html('<p onclick="x()">Hi</p>') == "<p>Hi</p>"
        with pytest.raises(RichTextTooLongError):
            clean_rich_html("<p>" + "x" * RICH_TEXT_MAX_CHARS + "</p>")

    def it_applies_to_a_body_that_may_be_plain_text():
        from core.html_sanitize import RICH_TEXT_MAX_CHARS, RichTextTooLongError, clean_rich_body

        with pytest.raises(RichTextTooLongError):
            clean_rich_body("x" * (RICH_TEXT_MAX_CHARS + 1))
        with pytest.raises(RichTextTooLongError):
            clean_rich_body("<p>" + "x" * RICH_TEXT_MAX_CHARS + "</p>")

    def it_applies_to_page_and_wiki_saves_markdown_included():
        from core.html_sanitize import RICH_TEXT_MAX_CHARS, RichTextTooLongError
        from membership.markdown import sanitize_page_submission, sanitize_wiki_submission

        for save in (sanitize_page_submission, sanitize_wiki_submission):
            with pytest.raises(RichTextTooLongError):
                save("**bold** " + "x" * RICH_TEXT_MAX_CHARS)
            with pytest.raises(RichTextTooLongError):
                save("<p>" + "x" * RICH_TEXT_MAX_CHARS + "</p>")


def describe_the_sanitizer_dependency():
    def it_uses_nh3_and_never_bleach():
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        requirements = (root / "requirements.txt").read_text().splitlines()
        assert any(line.startswith("nh3") for line in requirements)
        assert not any(line.startswith("bleach") for line in requirements)
        importers = [
            str(path.relative_to(root))
            for path in root.rglob("*.py")
            if ".venv" not in path.parts
            and any(
                line.lstrip().startswith(("import bleach", "from bleach")) for line in path.read_text().splitlines()
            )
        ]
        assert importers == []
