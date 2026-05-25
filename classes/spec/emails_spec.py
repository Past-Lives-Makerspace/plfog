"""BDD specs for classes/emails.py helpers."""

from __future__ import annotations


def describe_absolute_url():
    def it_prepends_book_base_url_to_path(settings):
        settings.BOOK_BASE_URL = "https://book.pastlives.space"
        from classes.emails import _absolute_url

        assert _absolute_url("/classes/my/abc123/") == "https://book.pastlives.space/classes/my/abc123/"

    def it_strips_trailing_slash_from_base(settings):
        settings.BOOK_BASE_URL = "https://book.pastlives.space/"
        from classes.emails import _absolute_url

        assert _absolute_url("/classes/my/token/") == "https://book.pastlives.space/classes/my/token/"

    def it_falls_back_to_default_when_setting_is_missing(settings):
        if hasattr(settings, "BOOK_BASE_URL"):
            delattr(settings, "BOOK_BASE_URL")
        from classes.emails import _absolute_url

        assert _absolute_url("/path/") == "https://book.pastlives.space/path/"
