"""BDD specs for the youtube_embed_id template filter."""

from __future__ import annotations

from classes.templatetags.classes_tags import youtube_embed_id


def describe_youtube_embed_id():
    def it_extracts_id_from_watch_url():
        assert youtube_embed_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def it_extracts_id_from_watch_url_with_extra_params():
        assert youtube_embed_id("https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=30") == "dQw4w9WgXcQ"

    def it_extracts_id_from_youtu_be_url():
        assert youtube_embed_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def it_extracts_id_from_youtu_be_with_query():
        assert youtube_embed_id("https://youtu.be/dQw4w9WgXcQ?t=12") == "dQw4w9WgXcQ"

    def it_extracts_id_from_embed_url():
        assert youtube_embed_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def it_extracts_id_from_shorts_url():
        assert youtube_embed_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def it_extracts_id_from_nocookie_embed():
        assert youtube_embed_id("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def it_returns_empty_for_non_youtube_url():
        assert youtube_embed_id("https://vimeo.com/123456789") == ""

    def it_returns_empty_for_empty_input():
        assert youtube_embed_id("") == ""

    def it_returns_empty_for_none():
        assert youtube_embed_id(None) == ""
