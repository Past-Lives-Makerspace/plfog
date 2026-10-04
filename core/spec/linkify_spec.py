"""BDD specs for core.linkify: bare addresses become hardened links, the way bleach.linkify did it.

Expected strings are bleach.linkify's own output for the same input, except where the module
docstring says the behaviour differs on purpose (a ``data:`` address, a match starting with a
hyphen, ``&nbsp;`` ending an address).
"""

from __future__ import annotations

import time

import pytest

from core.linkify import _match_end, _trim, _WORD_RE, linkify, linkify_text


def _mark(attrs: dict[str, str]) -> dict[str, str]:
    attrs["rel"] = "r"
    return attrs


def _link(href: str, text: str | None = None) -> str:
    return f'<a href="{href}" rel="r">{href if text is None else text}</a>'


def describe_linkify_text():
    def describe_what_becomes_a_link():
        def it_links_a_full_url():
            assert linkify_text("go http://a.com/x now", _mark) == f"go {_link('http://a.com/x')} now"

        def it_links_a_bare_domain_with_http_added():
            assert (
                linkify_text("visit www.example.com", _mark)
                == f"visit {_link('http://www.example.com', 'www.example.com')}"
            )

        def it_links_a_bare_domain_with_a_path_and_query():
            text = "example.org/path?q=1&amp;r=2"
            assert linkify_text(text, _mark) == _link(f"http://{text}", text)

        def it_links_a_query_straight_after_the_host():
            assert linkify_text("http://a.com?x", _mark) == _link("http://a.com?x")

        def it_links_a_hyphenated_host():
            assert linkify_text("x-y-z.com", _mark) == _link("http://x-y-z.com", "x-y-z.com")

        def it_links_a_country_code_after_a_known_domain():
            assert linkify_text("a.com.au", _mark) == _link("http://a.com.au", "a.com.au")

        def it_reads_the_top_level_domain_in_any_case():
            assert linkify_text("WWW.EXAMPLE.COM", _mark) == _link("http://WWW.EXAMPLE.COM", "WWW.EXAMPLE.COM")

        def it_links_a_host_with_a_letter_outside_ascii():
            assert linkify_text("café.com", _mark) == _link("http://café.com", "café.com")

        def it_ends_a_top_level_domain_at_a_hyphen():
            assert linkify_text("example.co-op", _mark) == _link("http://example.co", "example.co") + "-op"

        def it_keeps_user_and_password_in_the_link():
            assert linkify_text("http://user:pw@a.com/x", _mark) == _link("http://user:pw@a.com/x")

        def it_keeps_a_user_without_a_password():
            assert linkify_text("ftp://user@files.org", _mark) == _link("ftp://user@files.org")

        def it_links_a_typed_mailto_address():
            assert linkify_text("mailto:joe@example.com", _mark) == _link("mailto:joe@example.com")

        def it_keeps_a_port():
            assert linkify_text("http://a.com:8080/x", _mark) == _link("http://a.com:8080/x")

        def it_keeps_a_port_before_a_full_stop():
            assert linkify_text("a.com:80.", _mark) == _link("http://a.com:80", "a.com:80") + "."

        def it_leaves_a_port_followed_by_letters_outside():
            assert linkify_text("a.com:80x", _mark) == _link("http://a.com", "a.com") + ":80x"

        def it_leaves_a_colon_without_a_port_outside():
            assert linkify_text("a.com:", _mark) == _link("http://a.com", "a.com") + ":"

        def it_reads_the_host_after_more_than_three_slashes_as_a_bare_domain():
            assert linkify_text("https:////a.com", _mark) == "https:////" + _link("http://a.com", "a.com")

    def describe_what_stays_text():
        def it_leaves_a_domain_outside_the_known_list():
            assert linkify_text("see pastlives.space or foo.bar", _mark) == "see pastlives.space or foo.bar"

        def it_leaves_an_email_address():
            assert linkify_text("joe@example.com and a@b.com", _mark) == "joe@example.com and a@b.com"

        def it_leaves_an_email_address_with_a_hyphenated_domain():
            assert linkify_text("write to info@past-lives.org", _mark) == "write to info@past-lives.org"

        def it_leaves_a_data_address():
            assert linkify_text("data:text.com/html", _mark) == "data:text.com/html"

        def it_leaves_a_dotted_number():
            assert linkify_text("1.2.3.4", _mark) == "1.2.3.4"

        def it_leaves_a_domain_followed_by_more_labels():
            assert linkify_text("a.com.x", _mark) == "a.com.x"

        def it_leaves_an_empty_label():
            assert linkify_text("a..com and .com", _mark) == "a..com and .com"

        def it_leaves_a_scheme_with_no_host():
            assert linkify_text("http://", _mark) == "http://"

        def it_leaves_a_password_without_an_at_sign():
            assert linkify_text("http://user:pw", _mark) == "http://user:pw"

        def it_leaves_userinfo_with_no_host_after_it():
            assert linkify_text("http://user:pw@ and ftp://user@", _mark) == "http://user:pw@ and ftp://user@"

        def it_leaves_a_word_with_a_colon_that_is_no_scheme():
            assert linkify_text("note:a.com", _mark) == "note:" + _link("http://a.com", "a.com")

    def describe_punctuation_around_a_link():
        def it_leaves_a_trailing_comma_and_full_stop_outside():
            assert linkify_text("http://a.com/x,.", _mark) == _link("http://a.com/x") + ",."

        def it_leaves_enclosing_brackets_outside():
            assert linkify_text("(see http://a.com/x)", _mark) == f"(see {_link('http://a.com/x')})"

        def it_keeps_balanced_brackets_inside_the_path():
            assert linkify_text("((http://a.com/(x)))", _mark) == "((" + _link("http://a.com/(x)") + "))"

        def it_ends_an_address_at_a_non_breaking_space():
            assert linkify_text("http://a.com/x&nbsp;next", _mark) == _link("http://a.com/x") + "&nbsp;next"


def describe_linkify():
    def it_hardens_an_authors_link_and_keeps_its_attribute_order():
        html = '<a href="http://x.com" title="t">x</a>'
        assert linkify(html, _mark) == '<a href="http://x.com" title="t" rel="r">x</a>'

    def it_hardens_an_anchor_with_no_attributes():
        assert linkify("<a>x</a>", _mark) == '<a rel="r">x</a>'

    def it_leaves_the_text_of_an_existing_link_alone():
        assert linkify('<a href="/in">www.a.com</a> b.com', _mark) == (
            '<a href="/in" rel="r">www.a.com</a> ' + _link("http://b.com", "b.com")
        )

    def it_links_text_on_both_sides_of_an_anchor():
        assert linkify("<p>c.com<a>d.com</a>e.com</p>", _mark) == (
            "<p>" + _link("http://c.com", "c.com") + '<a rel="r">d.com</a>' + _link("http://e.com", "e.com") + "</p>"
        )

    def it_links_inside_code_as_bleach_did():
        assert linkify("<pre><code>run manage.py now</code></pre>", _mark) == (
            "<pre><code>run " + _link("http://manage.py", "manage.py") + " now</code></pre>"
        )

    def it_links_text_before_the_first_tag():
        assert linkify("a.com<br>", _mark) == _link("http://a.com", "a.com") + "<br>"

    def it_leaves_markup_without_addresses_unchanged():
        assert linkify("<p><b>bold</b></p>", _mark) == "<p><b>bold</b></p>"

    def it_stays_fast_on_long_runs_of_text_and_tags():
        hyphens = "a-" * 50_000
        dots = "a-a." * 25_000
        brackets = "http://a.com/" + ")" * 100_000
        tags = "<a>x</a>a.com " * 7_000
        started = time.monotonic()
        for text in (hyphens, dots, brackets):
            linkify_text(text, _mark)
        linkify(tags, _mark)
        assert time.monotonic() - started < 2


def describe_match_end():
    def it_fails_loudly_when_the_pattern_does_not_match():
        with pytest.raises(AssertionError):
            _match_end(_WORD_RE, "!", 0)


def describe_trim():
    def it_splits_brackets_and_punctuation_from_an_address():
        assert _trim("((a.com/x)),.") == ("((", "a.com/x", ")),.")

    def it_stops_when_brackets_are_all_there_is():
        assert _trim("(") == ("(", "", "")
        assert _trim("((") == ("((", "", "")
