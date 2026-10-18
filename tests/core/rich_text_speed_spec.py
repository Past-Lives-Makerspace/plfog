"""BDD specs: every rich text and Markdown entry point stays fast on large, deeply nested input.

Deeply nested input was slow under the old sanitizer. Each input here is generated to the
size limit a form or autosave accepts (``RICH_TEXT_MAX_CHARS``), and each call must finish
well inside a request.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from core import html_sanitize
from core.html_sanitize import RICH_TEXT_MAX_CHARS
from core.linkify import linkify
from membership import markdown

# Locally each call takes a fraction of a second. CI's test job runs under coverage and the
# mutation plugin, which put the deep inputs at 2.0 to 2.6 seconds, so the budget allows for
# that and still fails the quadratic blowup this spec exists to catch.
LIMIT_SECONDS = 5.0


def _fill(head: str, unit: str) -> str:
    """``head`` followed by as many whole ``unit`` copies as fit in the size limit."""
    return head + unit * ((RICH_TEXT_MAX_CHARS - len(head)) // len(unit))


INPUTS = {
    "nested_blocks": _fill("", "<blockquote></p>"),
    "many_formatting_tags": _fill("<p>" + "".join(f'<b x{i}="i">' for i in range(60)) + "</p>", "<p>x</p>"),
    "deep_quotes": _fill("", "<blockquote>"),
    "deep_lists": _fill("", "<ul><li>"),
    "many_tags": _fill("", '<p><b>x</b> <a href="http://a.com">a.com</a> b.com</p>'),
    # A non-breaking space is one character typed but six ("&nbsp;") once sanitized, and each
    # one ends a link's path; a scan that cut back from the end of the run went quadratic.
    "nbsp_in_paths": _fill("<p>", "a.co/\u00a0"),
    "nbsp_entity_in_paths": _fill("<p>", "a.co/&nbsp;"),
}

# The wiki's block rewrite (``membership.markdown._normalize_wiki_blocks``) runs on hostile
# editor HTML before the sanitizer, so these run against the wiki's HTML entry points.
# Tags that never close are the shape that sank the first version: a pattern spanning from
# each opener to a ">" rescanned the rest of the input from every one (100 seconds).
WIKI_HTML_INPUTS = {
    "code_block_lines": _fill('<div class="ql-code-block-container">', '<div class="ql-code-block">x</div>'),
    "code_block_unclosed": _fill('<div class="ql-code-block-container">', "<div>x<b>"),
    "checklist_items": _fill("<ol>", '<li data-list="unchecked"><br>'),
    "unclosed_code_containers": _fill("<p>", "<div ql-code-block-container "),
    "unclosed_divs": _fill("<p>", "<div "),
    "unclosed_ols": _fill("<p>", "<ol "),
    "container_then_unclosed_divs": _fill("<div class=ql-code-block-container>", "<div x"),
    "chained_unfinished_code_blocks": _fill("", "<div class=ql-code-block-container><div>x</div>"),
}
WIKI_HTML_ENTRY_POINTS = ("sanitize_wiki_html", "sanitize_wiki_submission", "render_wiki_content")

ENTRY_POINTS: dict[str, Callable[[str], object]] = {
    "sanitize_rich_html": html_sanitize.sanitize_rich_html,
    "clean_rich_html": html_sanitize.clean_rich_html,
    "clean_rich_body": html_sanitize.clean_rich_body,
    "render_rich_body": html_sanitize.render_rich_body,
    "render_rich_email_body": html_sanitize.render_rich_email_body,
    "render_rich_email_text": html_sanitize.render_rich_email_text,
    "rich_html_to_text": html_sanitize.rich_html_to_text,
    "rich_html_to_lines": html_sanitize.rich_html_to_lines,
    "render_markdown_member": markdown.render_markdown,
    "render_markdown_wiki": lambda source: markdown.render_markdown(source, profile="wiki"),
    "render_markdown_help": lambda source: markdown.render_markdown(source, profile="help"),
    "sanitize_page_html": markdown.sanitize_page_html,
    "sanitize_page_submission": markdown.sanitize_page_submission,
    "render_page_content": markdown.render_page_content,
    "sanitize_wiki_html": markdown.sanitize_wiki_html,
    "sanitize_wiki_submission": markdown.sanitize_wiki_submission,
    "render_wiki_content": markdown.render_wiki_content,
    "linkify": lambda html: linkify(html, lambda attrs: attrs),
}


# Plain text a crafted request can save as a wiki body. Python-Markdown is quadratic on each
# of these (unclosed "[" alone ran for minutes at the size limit), so a wiki save must never
# store them as Markdown; this is the save-then-view path a reader pays for.
WIKI_TEXT_INPUTS = {
    "open_brackets": _fill("", "[ "),
    "open_images": _fill("", "![ "),
    "open_link_targets": _fill("", "[a]( "),
    "open_footnotes": _fill("", "[^ "),
    "unclosed_tags": _fill("x ", "<ol "),
    "open_cdata": _fill("x ", "<![CDATA[ "),
}


def describe_rich_text_entry_points():
    def it_generates_every_input_at_the_size_limit():
        for value in INPUTS.values():
            assert RICH_TEXT_MAX_CHARS - 64 < len(value) <= RICH_TEXT_MAX_CHARS

    @pytest.mark.parametrize("shape", sorted(INPUTS))
    @pytest.mark.parametrize("entry_point", sorted(ENTRY_POINTS))
    def it_finishes_well_inside_a_request(entry_point: str, shape: str):
        started = time.perf_counter()
        ENTRY_POINTS[entry_point](INPUTS[shape])
        assert time.perf_counter() - started < LIMIT_SECONDS

    @pytest.mark.parametrize("shape", sorted(WIKI_HTML_INPUTS))
    @pytest.mark.parametrize("entry_point", WIKI_HTML_ENTRY_POINTS)
    def it_rewrites_hostile_wiki_html_well_inside_a_request(entry_point: str, shape: str):
        assert RICH_TEXT_MAX_CHARS - 64 < len(WIKI_HTML_INPUTS[shape]) <= RICH_TEXT_MAX_CHARS
        started = time.perf_counter()
        ENTRY_POINTS[entry_point](WIKI_HTML_INPUTS[shape])
        assert time.perf_counter() - started < LIMIT_SECONDS

    @pytest.mark.parametrize("shape", sorted(WIKI_TEXT_INPUTS))
    def it_saves_and_renders_hostile_wiki_text_well_inside_a_request(shape: str):
        assert RICH_TEXT_MAX_CHARS - 64 < len(WIKI_TEXT_INPUTS[shape]) <= RICH_TEXT_MAX_CHARS
        started = time.perf_counter()
        markdown.render_wiki_content(markdown.sanitize_wiki_submission(WIKI_TEXT_INPUTS[shape]))
        assert time.perf_counter() - started < LIMIT_SECONDS
