"""BDD specs: the three stored-body email templates render the rich body, formatted.

Renders each template with a hand-built context (Django's lenient missing-var handling
fills the rest), asserting that a heading/list/bold survives and is inline-styled in the
``.html`` part and flattened to plain text in the ``.txt`` part.
"""

from __future__ import annotations

from django.template.loader import render_to_string

_RICH = "<h2>Welcome</h2><p>Bring <strong>tools</strong>.</p><ul><li>Pencil</li></ul>"


def describe_class_welcome_email():
    def it_styles_the_rich_body_for_the_dark_card():
        html = render_to_string(
            "classes/emails/welcome.html",
            {"body": _RICH, "greeting_name": "Sam", "offering": {"title": "Woodworking"}},
        )
        assert "<h2" in html
        assert "margin:24px" in html  # heading inline-styled
        assert "font-weight:700" in html  # bold survives
        assert "Pencil" in html

    def it_flattens_the_body_in_the_text_part():
        txt = render_to_string(
            "classes/emails/welcome.txt",
            {"body": _RICH, "greeting_name": "Sam", "offering": {"title": "Woodworking"}},
        )
        assert "<h2" not in txt
        assert "- Pencil" in txt


def describe_orientation_thankyou_email():
    def it_styles_the_rich_body_for_the_dark_card():
        html = render_to_string(
            "membership/emails/orientation_thankyou.html",
            {"body": _RICH, "greeting_name": "Sam", "guild": {"name": "Forge"}, "guild_url": "https://x/g"},
        )
        assert "margin:24px" in html
        assert "Pencil" in html

    def it_flattens_the_body_in_the_text_part():
        txt = render_to_string(
            "membership/emails/orientation_thankyou.txt",
            {"body": _RICH, "greeting_name": "Sam", "guild": {"name": "Forge"}, "guild_url": "https://x/g"},
        )
        assert "<h2" not in txt
        assert "- Pencil" in txt
