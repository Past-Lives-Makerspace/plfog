"""BDD specs for the rich_text template filters (the safe-render choke point)."""

from __future__ import annotations

from django.utils.safestring import SafeString

from core.templatetags.rich_text import rich_email_body, rich_email_text


def describe_rich_email_body_filter():
    def it_returns_safe_sanitized_styled_html():
        out = rich_email_body("<h2>Hi</h2><script>evil()</script>")
        assert isinstance(out, SafeString)
        assert "<script" not in out
        assert "margin:24px" in out  # heading inline-styled for the dark card

    def it_treats_none_as_blank():
        assert rich_email_body(None) == ""


def describe_rich_email_text_filter():
    def it_returns_safe_flattened_plain_text():
        out = rich_email_text("<p>Hi</p><ul><li>one</li></ul>")
        assert isinstance(out, SafeString)
        assert "<" not in out
        assert "one" in out

    def it_treats_none_as_blank():
        assert rich_email_text(None) == ""
