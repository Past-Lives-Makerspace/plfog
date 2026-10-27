"""BDD specs for rich_body's "lists" mode (#952): plain text with paragraphs, line breaks and bullets.

A guild lead types plain text: blank lines between paragraphs, ``- `` lines for a list, emoji,
and a ``- Name`` sign off. The guild announcement card renders it through
``{{ body|rich_body:"lists" }}``; every other caller keeps the old output.
"""

from __future__ import annotations

import pytest
from django.template import Context, Template

from core.html_sanitize import render_rich_body
from core.templatetags.rich_text import rich_body

PENINA = (
    "Hello Writers!\n\nUp next: Last meeting of 2026!\n\nOur next meetup is October 24, 3:30–5:30pm.\n\n"
    "When we meet on the 24th, what would you like to do?\n\n- Create a character—human or non-human\n"
    "- Create a dialogue between two characters\n- Quiet co-writing\n\nPlease reply with your preference, "
    "or suggest something else!\n\n.\n\nZine 4 begins:\n\n✅ All members, including Writers Guild guests, "
    "can have their written work included.\n\n- Penina"
)


def describe_render_rich_body_with_lists():
    def it_renders_peninas_post_as_paragraphs_and_one_list():
        html = render_rich_body(PENINA, lists=True)

        assert html == (
            "<p>Hello Writers!</p><p>Up next: Last meeting of 2026!</p>"
            "<p>Our next meetup is October 24, 3:30–5:30pm.</p>"
            "<p>When we meet on the 24th, what would you like to do?</p>"
            "<ul><li>Create a character—human or non-human</li>"
            "<li>Create a dialogue between two characters</li><li>Quiet co-writing</li></ul>"
            "<p>Please reply with your preference, or suggest something else!</p><p>.</p>"
            "<p>Zine 4 begins:</p>"
            "<p>✅ All members, including Writers Guild guests, can have their written work included.</p>"
            "<p>- Penina</p>"
        )

    def it_keeps_a_single_line_break_as_a_break():
        assert render_rich_body("Saturday\n3 to 5pm", lists=True) == "<p>Saturday<br>3 to 5pm</p>"

    def it_splits_a_paragraph_around_a_list_inside_it():
        html = render_rich_body("Bring:\n- paper\n  - pens\nSee you there\nsoon", lists=True)

        assert html == "<p>Bring:</p><ul><li>paper</li><li>pens</li></ul><p>See you there<br>soon</p>"

    def it_leaves_a_lone_dash_line_inside_a_paragraph_as_text():
        assert render_rich_body("Thanks all\n- Penina", lists=True) == "<p>Thanks all<br>- Penina</p>"

    def it_does_not_treat_a_bare_dash_or_a_hyphenated_word_as_a_bullet():
        assert render_rich_body("-\n-\nwell-known", lists=True) == "<p>-<br>-<br>well-known</p>"

    def it_breaks_paragraphs_on_a_blank_line_that_holds_spaces():
        assert render_rich_body("One\n   \nTwo\n\t\n\n- a\n- b", lists=True) == (
            "<p>One</p><p>Two</p><ul><li>a</li><li>b</li></ul>"
        )

    def it_reads_windows_and_old_mac_line_ends():
        assert render_rich_body("One\r\nTwo\r\n\r\n- a\r- b\r\rThree", lists=True) == (
            "<p>One<br>Two</p><ul><li>a</li><li>b</li></ul><p>Three</p>"
        )

    def it_skips_leading_and_trailing_blank_lines():
        assert render_rich_body("\n\n  \nHi\n\n\n", lists=True) == "<p>Hi</p>"

    def it_escapes_html_in_text_and_in_list_items():
        html = render_rich_body("<script>x</script>\n\n- <img src=x onerror=alert(1)>\n- ok", lists=True)

        assert "<script>" not in html
        assert "<img" not in html
        assert html.startswith("<p>&lt;script&gt;x&lt;/script&gt;</p><ul><li>&lt;img")

    def it_escapes_a_body_that_looks_like_editor_html_instead_of_sanitizing_it():
        html = render_rich_body("<p><a href=https://evil.example>pay</a></p>", lists=True)

        assert html == "<p>&lt;p&gt;&lt;a href=https://evil.example&gt;pay&lt;/a&gt;&lt;/p&gt;</p>"
        assert "<a" not in html

    def it_escapes_a_tag_named_in_plain_text_and_still_makes_the_list():
        assert render_rich_body("Line one\nSee the <br> tag\n\n- a\n- b", lists=True) == (
            "<p>Line one<br>See the &lt;br&gt; tag</p><ul><li>a</li><li>b</li></ul>"
        )

    def it_returns_nothing_for_a_blank_body():
        assert render_rich_body("  \n ", lists=True) == ""

    def it_leaves_every_other_caller_without_lists():
        assert render_rich_body("- a\n- b") == "<p>- a<br>- b</p>"


def describe_rich_body_filter():
    def it_renders_lists_when_asked():
        out = Template('{% load rich_text %}{{ body|rich_body:"lists" }}').render(Context({"body": "- a\n- b"}))

        assert out == "<ul><li>a</li><li>b</li></ul>"

    def it_renders_paragraphs_without_a_mode():
        assert rich_body("- a\n- b") == "<p>- a<br>- b</p>"

    def it_refuses_an_unknown_mode():
        with pytest.raises(ValueError, match="Unknown rich_body mode 'bullets'"):
            rich_body("- a", "bullets")
