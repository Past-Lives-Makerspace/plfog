"""BDD specs for the youtube_embed_id and video_link template filters."""

from __future__ import annotations

from classes.templatetags.classes_tags import video_link, youtube_embed_id


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

    def it_returns_empty_for_a_lookalike_host():
        assert youtube_embed_id("https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ") == ""

    def it_returns_empty_for_another_providers_video():
        # A guild's own video field embeds YouTube and nothing else, so an Instagram
        # link there renders no player rather than a broken one.
        assert youtube_embed_id("https://www.instagram.com/reel/CxYzAbCdEfG/") == ""

    def it_returns_empty_for_empty_input():
        assert youtube_embed_id("") == ""

    def it_returns_empty_for_none():
        assert youtube_embed_id(None) == ""


def describe_video_link():
    def it_resolves_a_youtube_link_to_an_embed():
        link = video_link("https://youtu.be/dQw4w9WgXcQ")
        assert link.provider.name == "YouTube"
        assert link.is_embed
        assert link.embed_src == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"

    def it_resolves_an_instagram_link_to_a_card():
        link = video_link("https://www.instagram.com/reel/CxYzAbCdEfG/")
        assert link.provider.name == "Instagram"
        assert not link.is_embed

    def it_resolves_a_facebook_link_to_a_card():
        link = video_link("https://www.facebook.com/watch/?v=1234567890")
        assert link.provider.name == "Facebook"
        assert not link.is_embed

    def it_is_falsy_for_a_link_no_provider_owns():
        assert video_link("https://vimeo.com/123456789") is None

    def it_is_falsy_for_a_blank_field():
        assert video_link("") is None
