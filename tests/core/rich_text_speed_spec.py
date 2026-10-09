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
